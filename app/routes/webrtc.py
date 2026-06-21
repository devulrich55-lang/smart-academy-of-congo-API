"""WebRTC — signalisation temps réel (sans Jitsi)."""

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.deps import get_current_user, require_roles
from app.services.user_service import find_user_by_id
from app.services.webrtc_signaling import hub, live_signals
from app.utils.tokens import verify_access_token

router = APIRouter(prefix="/webrtc", tags=["webrtc"])
PUBLISH_ROLES = ("professeur", "assistant", "section", "universite", "ministere")


async def _broadcast_participants(room_key: str) -> None:
    participants = await hub.list_participants(room_key)
    payload = {"type": "participants", "list": participants}
    for pid in await hub.peer_ids(room_key):
        ws_peer = await hub.get_peer_ws(room_key, pid)
        if ws_peer:
            try:
                await _send(ws_peer, payload)
            except Exception:
                pass


def _authenticate_token(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        decoded = verify_access_token(token)
        return find_user_by_id(decoded["sub"])
    except ValueError:
        return None


async def _send(ws: WebSocket, payload: dict) -> None:
    await ws.send_text(json.dumps(payload))


@router.get("/signals")
async def list_live_signals(user: dict = Depends(get_current_user)):
    signals = await live_signals.list_for_user(user)
    return {"signals": signals}


@router.post("/signal")
async def publish_live_signal(body: dict, user: dict = Depends(require_roles(*PUBLISH_ROLES))):
    session_id = str(body.get("sessionId") or body.get("id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail={"error": "SESSION_ID_REQUIRED"})
    payload = {
        **body,
        "sessionId": session_id,
        "hostEmail": user.get("email"),
        "hostName": body.get("hostName") or user.get("displayName") or user.get("email"),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    if body.get("kind") == "course":
        payload["inviteStudents"] = True
    if body.get("kind") == "meeting" and body.get("type") == "conference":
        payload["inviteStudents"] = True
    signal = await live_signals.publish(payload)
    return {"ok": True, "signal": signal}


@router.delete("/signal/{session_id}")
async def clear_live_signal(session_id: str, user: dict = Depends(require_roles(*PUBLISH_ROLES))):
    del user
    await live_signals.remove(session_id)
    return {"ok": True}


@router.websocket("/room/{room_id}")
async def webrtc_room(
    websocket: WebSocket,
    room_id: str,
    token: str | None = Query(None),
):
    cookie_token = websocket.cookies.get("sac_access")
    user = _authenticate_token(token or cookie_token)
    if not user:
        await websocket.close(code=4001, reason="AUTH_REQUIRED")
        return

    room_key = (room_id or "").strip()[:120]
    if not room_key:
        await websocket.close(code=4002, reason="INVALID_ROOM")
        return

    await websocket.accept()
    peer_id = str(uuid.uuid4())
    display_name = (
        user.get("displayName")
        or user.get("nom")
        or user.get("email")
        or "Participant SAC"
    )
    user_id = user.get("id") or ""
    role = user.get("role") or ""

    peers, chat_log, qa_log = await hub.register(
        room_key,
        peer_id,
        display_name,
        user_id,
        role,
        websocket,
    )

    await _send(
        websocket,
        {
            "type": "welcome",
            "peerId": peer_id,
            "displayName": display_name,
            "role": role,
            "peers": peers,
            "chatLog": chat_log,
            "qaLog": qa_log,
        },
    )

    for other in peers:
        try:
            ws_other = await hub.get_peer_ws(room_key, other["peerId"])
            if ws_other:
                await _send(
                    ws_other,
                    {
                        "type": "peer-joined",
                        "peerId": peer_id,
                        "displayName": display_name,
                        "role": role,
                    },
                )
        except Exception:
            pass

    await _broadcast_participants(room_key)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")
            target = msg.get("target")

            if msg_type in ("offer", "answer", "ice"):
                if not target:
                    continue
                ws_target = await hub.get_peer_ws(room_key, target)
                if ws_target:
                    payload = {**msg, "from": peer_id}
                    await _send(ws_target, payload)
                continue

            if msg_type == "chat":
                text = str(msg.get("text") or "").strip()[:2000]
                if not text:
                    continue
                chat_msg = {
                    "id": str(uuid.uuid4()),
                    "peerId": peer_id,
                    "displayName": display_name,
                    "text": text,
                    "at": datetime.now(timezone.utc).isoformat(),
                }
                await hub.add_chat(room_key, chat_msg)
                peer_ids = await hub.peer_ids(room_key)
                for pid in peer_ids:
                    ws_peer = await hub.get_peer_ws(room_key, pid)
                    if ws_peer:
                        try:
                            await _send(ws_peer, {"type": "chat", "message": chat_msg})
                        except Exception:
                            pass
                continue

            if msg_type == "mic-state":
                mic_on = bool(msg.get("micOn"))
                payload = {
                    "type": "mic-state",
                    "peerId": peer_id,
                    "micOn": mic_on,
                    "displayName": display_name,
                    "role": role,
                }
                peer_ids = await hub.peer_ids(room_key)
                for pid in peer_ids:
                    ws_peer = await hub.get_peer_ws(room_key, pid)
                    if ws_peer:
                        try:
                            await _send(ws_peer, payload)
                        except Exception:
                            pass
                continue

            if msg_type == "qa-question":
                text = str(msg.get("text") or "").strip()[:1000]
                if not text:
                    continue
                qa_msg = {
                    "kind": "question",
                    "id": str(msg.get("id") or uuid.uuid4()),
                    "text": text,
                    "author": str(msg.get("author") or display_name)[:120],
                    "authorEmail": str(msg.get("authorEmail") or user.get("email") or "")[:120],
                    "peerId": peer_id,
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                }
                await hub.add_qa(room_key, qa_msg)
                peer_ids = await hub.peer_ids(room_key)
                for pid in peer_ids:
                    ws_peer = await hub.get_peer_ws(room_key, pid)
                    if ws_peer:
                        try:
                            await _send(ws_peer, {"type": "qa", "message": qa_msg})
                        except Exception:
                            pass
                continue

            if msg_type == "qa-answer":
                qa_id = str(msg.get("questionId") or msg.get("id") or "").strip()
                answer = str(msg.get("answer") or "").strip()[:2000]
                if not qa_id or not answer:
                    continue
                qa_msg = {
                    "kind": "answer",
                    "id": qa_id,
                    "answer": answer,
                    "answeredBy": display_name,
                    "answeredAt": datetime.now(timezone.utc).isoformat(),
                }
                await hub.add_qa(room_key, qa_msg)
                peer_ids = await hub.peer_ids(room_key)
                for pid in peer_ids:
                    ws_peer = await hub.get_peer_ws(room_key, pid)
                    if ws_peer:
                        try:
                            await _send(ws_peer, {"type": "qa", "message": qa_msg})
                        except Exception:
                            pass
                continue

            if msg_type == "ping":
                await _send(websocket, {"type": "pong"})
                continue

    except WebSocketDisconnect:
        pass
    finally:
        remaining = await hub.unregister(room_key, peer_id)
        await _broadcast_participants(room_key)
        for pid in remaining:
            ws_peer = await hub.get_peer_ws(room_key, pid)
            if ws_peer:
                try:
                    await _send(ws_peer, {"type": "peer-left", "peerId": peer_id})
                except Exception:
                    pass
