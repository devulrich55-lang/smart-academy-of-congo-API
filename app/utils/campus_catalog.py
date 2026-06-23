"""
Catalogue officiel des établissements SAC — aligné sur js/sac-universities.js
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

UNIVERSITIES = [
    {"id": "unkin", "name": "Université de Kinshasa", "sigle": "UNIKIN", "type": "universite"},
    {"id": "unilu", "name": "Université de Lubumbashi", "sigle": "UNILU", "type": "universite"},
    {"id": "unikis", "name": "Université de Kisangani", "sigle": "UNIKIS", "type": "universite"},
    {"id": "upn", "name": "Université Pédagogique Nationale", "sigle": "UPN", "type": "universite"},
    {"id": "unigom", "name": "Université de Goma", "sigle": "UNIGOM", "type": "universite"},
    {"id": "unibuk", "name": "Université de Bukavu", "sigle": "UNIBUK", "type": "universite"},
    {"id": "uom", "name": "Université Officielle de Mbuji-Mayi", "sigle": "UOM", "type": "universite"},
    {"id": "unikan", "name": "Université de Kananga", "sigle": "UNIKAN", "type": "universite"},
    {"id": "uniknd", "name": "Université de Kindu", "sigle": "UNIKND", "type": "universite"},
    {"id": "unkwt", "name": "Université de Kikwit", "sigle": "UNKWT", "type": "universite"},
    {"id": "upro", "name": "Université Protestante au Congo", "sigle": "UPC", "type": "universite"},
    {"id": "ucc", "name": "Université Catholique du Congo", "sigle": "UCC", "type": "universite"},
    {"id": "ulk", "name": "Université Libre de Kinshasa", "sigle": "ULK", "type": "universite"},
    {"id": "usk", "name": "Université Simon Kimbangu", "sigle": "USK", "type": "universite"},
    {"id": "uccm", "name": "Université Chrétienne Cardinal Malula", "sigle": "UCCM", "type": "universite"},
]

INSTITUTES = [
    {"id": "istap", "name": "Institut Supérieur des Techniques Appliquées", "sigle": "ISTA", "type": "institut"},
    {"id": "inbat", "name": "Institut National du Bâtiment et Travaux Publics", "sigle": "INBTP", "type": "institut"},
    {
        "id": "ifsic",
        "name": "Institut Facultaire des Sciences de l'Information et de la Communication",
        "sigle": "IFSIC",
        "type": "institut",
    },
    {"id": "isck", "name": "Institut Supérieur de Commerce de Kinshasa", "sigle": "ISC-Kin", "type": "institut"},
    {"id": "aba", "name": "Académie des Beaux-Arts", "sigle": "ABA", "type": "institut"},
    {"id": "inarts", "name": "Institut National des Arts", "sigle": "INA", "type": "institut"},
    {
        "id": "istmed",
        "name": "Institut Supérieur des Techniques Médicales de Kinshasa",
        "sigle": "ISTM-Kin",
        "type": "institut",
    },
    {
        "id": "istmmayi",
        "name": "Institut Supérieur des Techniques Médicales de Mbuji-Mayi",
        "sigle": "ISTM MBUJIMAYI",
        "type": "institut",
    },
    {"id": "isstat", "name": "Institut Supérieur des Statistiques", "sigle": "ISS", "type": "institut"},
    {"id": "isau", "name": "Institut Supérieur d'Architecture et d'Urbanisme", "sigle": "ISAU", "type": "institut"},
    {"id": "isam", "name": "Institut Supérieur des Arts et Métiers", "sigle": "ISAM", "type": "institut"},
]

CATALOG = UNIVERSITIES + INSTITUTES
_BY_ID = {item["id"]: item for item in CATALOG}


def _norm_key(value: str | None) -> str:
    s = str(value or "").strip().lower()
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def get_by_id(campus_id: str | None) -> dict | None:
    if not campus_id:
        return None
    key = str(campus_id).strip().lower()
    return _BY_ID.get(key)


def resolve_campus_id(raw: str | None) -> str | None:
    """Identifiant canonique (unkin, unilu…) depuis id, sigle ou nom."""
    if not raw:
        return None
    key = _norm_key(raw)
    if not key:
        return None
    if key == "autre":
        return "autre"
    if key in _BY_ID:
        return key
    for item in CATALOG:
        if _norm_key(item.get("sigle")) == key or _norm_key(item.get("name")) == key:
            return item["id"]
    for item in CATALOG:
        sig = _norm_key(item.get("sigle"))
        if sig and (key in sig or sig in key):
            return item["id"]
    return key


def same_campus(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return resolve_campus_id(a) == resolve_campus_id(b)


def registered_campus(user: dict | None) -> str | None:
    if not user:
        return None
    role = user.get("role")
    if role in ("ministere", "superadmin"):
        return None
    raw = (
        user.get("universite")
        or user.get("universiteLocked")
        or user.get("sigle")
        or user.get("codeUni")
    )
    if not raw:
        return None
    return resolve_campus_id(str(raw))


def normalize_profile_campus(profile: dict | None) -> dict:
    if not profile:
        return profile or {}
    out = dict(profile)
    role = out.get("role")
    if role in ("ministere", "superadmin"):
        return out
    raw = (
        out.get("universite")
        or out.get("universiteLocked")
        or out.get("sigle")
        or out.get("codeUni")
    )
    if not raw:
        return out
    campus_id = resolve_campus_id(str(raw))
    if not campus_id:
        return out
    out["universite"] = campus_id
    out["universiteLocked"] = campus_id
    item = get_by_id(campus_id)
    if item and role == "universite":
        out["nomUniversite"] = out.get("nomUniversite") or item["name"]
        out["sigle"] = item["sigle"]
        if not out.get("codeUni"):
            out["codeUni"] = f"SAC-{item['sigle']}-{datetime.now().year}"
    return out
