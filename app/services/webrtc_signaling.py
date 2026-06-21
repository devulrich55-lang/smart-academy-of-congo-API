"""Signalisation WebRTC SAC — relais WebSocket en mémoire (mesh P2P)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket


@dataclass
class Peer:
    peer_id: str
    display_name: str
    user_id: str
    role: str
    websocket: WebSocket


@dataclass
class Room:
    room_id: str
    peers: dict[str, Peer] = field(default_factory=dict)
    chat_log: list[dict[str, Any]] = field(default_factory=list)
    qa_log: list[dict[str, Any]] = field(default_factory=list)


class WebRtcSignalingHub:
    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        room_id: str,
        peer_id: str,
        display_name: str,
        user_id: str,
        role: str,
        websocket: WebSocket,
    ) -> tuple[list[dict[str, str]], list[dict[str, Any]], list[dict[str, Any]]]:
        async with self._lock:
            room = self._rooms.setdefault(room_id, Room(room_id=room_id))
            room.peers[peer_id] = Peer(
                peer_id=peer_id,
                display_name=display_name,
                user_id=user_id,
                role=role,
                websocket=websocket,
            )
            peers = [
                {"peerId": p.peer_id, "displayName": p.display_name, "role": p.role}
                for pid, p in room.peers.items()
                if pid != peer_id
            ]
            return peers, list(room.chat_log), list(room.qa_log)

    async def unregister(self, room_id: str, peer_id: str) -> list[str]:
        async with self._lock:
            room = self._rooms.get(room_id)
            if not room:
                return []
            room.peers.pop(peer_id, None)
            remaining = list(room.peers.keys())
            if not room.peers:
                self._rooms.pop(room_id, None)
            return remaining

    async def get_peer_ws(self, room_id: str, peer_id: str) -> WebSocket | None:
        async with self._lock:
            room = self._rooms.get(room_id)
            if not room:
                return None
            peer = room.peers.get(peer_id)
            return peer.websocket if peer else None

    async def add_chat(self, room_id: str, message: dict[str, Any]) -> None:
        async with self._lock:
            room = self._rooms.get(room_id)
            if not room:
                return
            room.chat_log.append(message)
            if len(room.chat_log) > 200:
                room.chat_log = room.chat_log[-200:]

    async def add_qa(self, room_id: str, message: dict[str, Any]) -> None:
        async with self._lock:
            room = self._rooms.get(room_id)
            if not room:
                return
            qa_id = str(message.get("id") or "")
            if message.get("kind") == "answer" and qa_id:
                for item in room.qa_log:
                    if item.get("id") == qa_id:
                        item["answer"] = message.get("answer") or ""
                        item["answeredAt"] = message.get("answeredAt") or ""
                        item["answeredBy"] = message.get("answeredBy") or ""
                        return
                return
            room.qa_log.insert(0, message)
            if len(room.qa_log) > 100:
                room.qa_log = room.qa_log[:100]

    async def peer_ids(self, room_id: str, exclude: str | None = None) -> list[str]:
        async with self._lock:
            room = self._rooms.get(room_id)
            if not room:
                return []
            return [pid for pid in room.peers if pid != exclude]

    async def list_participants(self, room_id: str) -> list[dict[str, str]]:
        async with self._lock:
            room = self._rooms.get(room_id)
            if not room:
                return []
            return [
                {
                    "peerId": p.peer_id,
                    "displayName": p.display_name,
                    "role": p.role,
                    "userId": p.user_id,
                }
                for p in room.peers.values()
            ]


class LiveSignalStore:
    """Signaux d'appel live partagés entre utilisateurs (mémoire serveur)."""

    def __init__(self) -> None:
        self._signals: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def publish(self, signal: dict) -> dict:
        async with self._lock:
            sid = str(signal.get("sessionId") or "")
            if not sid:
                return signal
            self._signals[sid] = {**signal, "sessionId": sid}
            return self._signals[sid]

    async def remove(self, session_id: str) -> None:
        async with self._lock:
            self._signals.pop(session_id, None)

    async def list_for_user(self, user: dict) -> list[dict]:
        async with self._lock:
            out = []
            for sig in self._signals.values():
                if _user_matches_signal(user, sig):
                    out.append(dict(sig))
            return out


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _filiere_match(a: str | None, b: str | None) -> bool:
    sa, sb = _norm(a), _norm(b)
    if not sa or not sb:
        return True
    return sa == sb or sa in sb or sb in sa


def _user_matches_signal(user: dict, sig: dict) -> bool:
    role = user.get("role") or ""
    email = _norm(user.get("email"))
    kind = sig.get("kind")

    if kind == "course":
        if role != "etudiant":
            return False
        if sig.get("universite") and user.get("universite"):
            if _norm(sig["universite"]) != _norm(user["universite"]):
                return False
        if not _filiere_match(user.get("filiere"), sig.get("filiere")):
            return False
        if sig.get("niveau") and user.get("niveau"):
            if _norm(sig["niveau"]) != _norm(user["niveau"]):
                return False
        return True

    if kind == "meeting":
        if role == "universite":
            return True
        allowed = [_norm(e) for e in sig.get("allowedEmails") or []]
        if email and email in allowed:
            return True
        if _norm(sig.get("hostEmail")) == email:
            return True
        if role in ("professeur", "assistant", "section"):
            return True
        if role == "etudiant" and sig.get("inviteStudents"):
            if sig.get("universite") and user.get("universite"):
                if _norm(sig["universite"]) != _norm(user["universite"]):
                    return False
            return _filiere_match(user.get("filiere"), sig.get("filiere"))
        return False

    if kind == "ministry":
        if role != "universite":
            return False
        invited = sig.get("invitedUniversities") or sig.get("allowedEmails") or []
        if not invited:
            return True
        uni = _norm(user.get("universite"))
        return any(_norm(x) == uni or _norm(x) == email for x in invited)

    return False


hub = WebRtcSignalingHub()
live_signals = LiveSignalStore()
