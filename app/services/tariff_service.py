import json

from datetime import datetime, timezone



from app.database import get_db, row_to_user



DEFAULT_CDF_PER_USD = 2300

DEFAULT_PLATFORM_FEES = {
    "etudiant": {"amount": 1, "currency": "USD", "label": "Étudiant"},
    "assistant": {"amount": 5, "currency": "USD", "label": "Assistant"},
    "professeur": {"amount": 10, "currency": "USD", "label": "Professeur"},
    "universite": {"amount": 20, "currency": "USD", "label": "Université"},
}

PLATFORM_ROLES = list(DEFAULT_PLATFORM_FEES.keys())

DEFAULT_CAMPUS_TARIFFS = {

    "etudiant": {"amount": 1, "currency": "USD", "label": "Étudiant"},

    "assistant": {"amount": 5, "currency": "USD", "label": "Assistant"},

    "professeur": {"amount": 10, "currency": "USD", "label": "Professeur"},

}



ROLES_WITH_CAMPUS_TARIFF = ["etudiant", "professeur", "assistant"]





def to_cdf(amount_usd: float, cdf_per_usd: int = DEFAULT_CDF_PER_USD) -> int:

    return round(float(amount_usd) * cdf_per_usd)





def normalize_tariff_entry(

    role: str, raw: dict | None, cdf_per_usd: int = DEFAULT_CDF_PER_USD

) -> dict | None:

    if role not in ROLES_WITH_CAMPUS_TARIFF:

        return None

    default = DEFAULT_CAMPUS_TARIFFS[role]

    if not raw or not isinstance(raw, dict):

        return {

            "amount": default["amount"],

            "currency": "USD",

            "cdf": to_cdf(default["amount"], cdf_per_usd),

            "label": default["label"],

        }

    amount = float(raw.get("amount", 0))

    if not (0.5 <= amount <= 500):

        raise ValueError("INVALID_TARIFF_AMOUNT")

    return {

        "amount": round(amount, 2),

        "currency": "USD",

        "cdf": to_cdf(amount, cdf_per_usd),

        "label": default["label"],

    }





def build_campus_tariff_pack(raw: dict | None) -> dict:

    data = raw if isinstance(raw, dict) else {}

    pack = {}

    for role in ROLES_WITH_CAMPUS_TARIFF:

        pack[role] = normalize_tariff_entry(role, data.get(role))

    return pack





def parse_campus_tariffs(data) -> dict | None:

    if not data:

        return None

    try:

        parsed = json.loads(data) if isinstance(data, str) else data

        if not isinstance(parsed, dict):

            return None

        return build_campus_tariff_pack(parsed)

    except (json.JSONDecodeError, ValueError):

        return None





def find_university_by_code(code: str | None) -> dict | None:

    if not code:

        return None

    ident = code.strip().lower()

    rows = get_db().execute(

        "SELECT * FROM users WHERE role = 'universite'"

    ).fetchall()

    for row in rows:

        u = row_to_user(row)

        keys = [

            str(k).strip().lower()

            for k in (u.get("universite"), u.get("sigle"), u.get("codeUni"))

            if k

        ]

        if ident in keys:

            return u

    return None





def get_campus_tariffs_for_university(universite_code: str) -> dict:

    uni = find_university_by_code(universite_code)

    custom = uni.get("campusTariffs") if uni else None

    pack = build_campus_tariff_pack(custom)

    merged = {role: pack[role] for role in ROLES_WITH_CAMPUS_TARIFF}

    academic_raw = uni.get("campusAcademicFees") if uni else None

    try:

        academic = normalize_academic_fees_entry(academic_raw)

    except ValueError:

        academic = normalize_academic_fees_entry(None)

    return {

        "universite": universite_code,

        "universityName": uni.get("nomUniversite") if uni else None,

        "configured": bool(custom),

        "tariffs": merged,

        "academicFees": academic,

    }





def get_campus_fee(universite_code: str, role: str) -> dict:

    pack = get_campus_tariffs_for_university(universite_code)

    return pack["tariffs"].get(role) or normalize_tariff_entry("etudiant", None)





def validate_tariffs_payload(body: dict) -> dict:

    if not body or not isinstance(body, dict):

        raise ValueError("INVALID_TARIFFS")

    out = {}

    for role in ROLES_WITH_CAMPUS_TARIFF:

        if body.get(role) is not None:

            out[role] = normalize_tariff_entry(role, body[role])

    if not out:

        raise ValueError("INVALID_TARIFFS")

    return out





def _member_matches_university(row, universite_code: str) -> bool:

    code = str(universite_code or "").strip().lower()

    if not code:

        return False

    keys = [

        str(k).strip().lower()

        for k in (row["universite"], row["sigle"], row["code_uni"])

        if k

    ]

    return code in keys





def sync_campus_tariffs_to_members(universite_code: str, tariffs: dict) -> int:

    if not universite_code or not tariffs:

        return 0

    db = get_db()

    now = datetime.now(timezone.utc).isoformat()

    rows = db.execute(

        """SELECT id, role, universite, sigle, code_uni FROM users

           WHERE role IN ('etudiant','professeur','assistant')"""

    ).fetchall()

    updated = 0

    for row in rows:

        if not _member_matches_university(row, universite_code):

            continue

        fee = tariffs.get(row["role"])

        if not fee:

            continue

        db.execute(

            "UPDATE users SET inscription_fee = ?, updated_at = ? WHERE id = ?",

            (json.dumps(fee), now, row["id"]),

        )

        updated += 1

    db.commit()

    return updated





def update_university_campus_tariffs(user_id: str, partial_tariffs: dict) -> dict:

    row = get_db().execute(

        "SELECT * FROM users WHERE id = ?", (user_id,)

    ).fetchone()

    user = row_to_user(row)

    if not user or user.get("role") != "universite":

        raise ValueError("FORBIDDEN")



    existing_raw = {}

    if row["campus_tariffs"]:

        try:

            existing_raw = json.loads(row["campus_tariffs"])

            if not isinstance(existing_raw, dict):

                existing_raw = {}

        except json.JSONDecodeError:

            existing_raw = {}



    merged_raw = {**existing_raw}

    for role in ROLES_WITH_CAMPUS_TARIFF:

        if partial_tariffs.get(role) is not None:

            merged_raw[role] = partial_tariffs[role]



    next_tariffs = build_campus_tariff_pack(merged_raw)

    now = datetime.now(timezone.utc).isoformat()

    get_db().execute(

        "UPDATE users SET campus_tariffs = ?, updated_at = ? WHERE id = ?",

        (json.dumps(next_tariffs), now, user_id),

    )

    get_db().commit()



    uni_code = user.get("universite") or user.get("sigle") or user.get("codeUni")

    pack = get_campus_tariffs_for_university(uni_code)

    members_updated = sync_campus_tariffs_to_members(uni_code, pack["tariffs"])

    return {

        **pack,

        "membersUpdated": members_updated,

    }


DEFAULT_ACADEMIC_TRIMESTRE = 150
ACADEMIC_FEE_MIN = 1
ACADEMIC_FEE_MAX = 50000

FEE_CATEGORY_DEFS = [
    {"key": "frais_academiques", "label": "Frais académiques", "term": "Année académique", "defaultAmount": 150},
    {"key": "enrolement", "label": "Frais d'enrôlement", "term": "Année académique", "defaultAmount": 80},
    {"key": "reinscription", "label": "Frais de réinscription", "term": "Année académique", "defaultAmount": 60},
    {"key": "minerval", "label": "Minerval", "term": "Année académique", "defaultAmount": 500},
    {"key": "inscription_univ", "label": "Inscription universitaire", "term": "Année académique", "defaultAmount": 50},
    {"key": "bibliotheque", "label": "Bibliothèque", "term": "Année académique", "defaultAmount": 30},
    {"key": "laboratoire", "label": "Laboratoire", "term": "Année académique", "defaultAmount": 20},
]


def _normalize_category_amount(val, fallback: float) -> float:
    if val is None or val == "":
        return 0.0
    try:
        n = float(val)
    except (TypeError, ValueError):
        return fallback
    if n == 0:
        return 0.0
    if ACADEMIC_FEE_MIN <= n <= ACADEMIC_FEE_MAX:
        return round(n, 2)
    return fallback


def normalize_academic_fees_entry(raw: dict | None) -> dict:
    data = raw if isinstance(raw, dict) else {}
    trim_val = (
        (data.get("trimestre") or {}).get("amount")
        if isinstance(data.get("trimestre"), dict)
        else None
    )
    if trim_val is None and isinstance(data.get("t1"), dict):
        trim_val = data.get("t1", {}).get("amount")
    amount = float(trim_val if trim_val is not None else DEFAULT_ACADEMIC_TRIMESTRE)
    if not (ACADEMIC_FEE_MIN <= amount <= ACADEMIC_FEE_MAX):
        raise ValueError("INVALID_ACADEMIC_FEE_AMOUNT")
    amount = round(amount, 2)
    entry = {"amount": amount, "currency": "USD"}
    src_cats = data.get("categories") if isinstance(data.get("categories"), dict) else {}
    categories = {}
    for defn in FEE_CATEGORY_DEFS:
        key = defn["key"]
        cat_entry = src_cats.get(key) if isinstance(src_cats.get(key), dict) else {}
        cat_amount = _normalize_category_amount(
            cat_entry.get("amount"),
            defn["defaultAmount"] if src_cats else (amount if key in ("minerval", "frais_academiques") else defn["defaultAmount"]),
        )
        categories[key] = {
            "amount": cat_amount,
            "currency": "USD",
            "label": defn["label"],
        }
    return {
        "trimestre": entry,
        "t1": entry,
        "t2": entry,
        "t3": entry,
        "categories": categories,
        "useCategories": bool(src_cats) or "categories" in data,
    }


def validate_academic_fees_payload(body: dict) -> dict:
    if not body or not isinstance(body, dict):
        raise ValueError("INVALID_ACADEMIC_FEES")
    return normalize_academic_fees_entry(body)


def build_university_fees_for_student(
    academic_fees: dict,
    payment: dict | None,
    inscription_amount: float,
    cdf_per_usd: int,
) -> list:
    year = datetime.now(timezone.utc).year
    trim = float(academic_fees["trimestre"]["amount"])
    paid_inscription = bool(
        payment
        and (
            payment.get("status") in ("verified", "pending_verification")
            or payment.get("paidAt")
        )
    )
    paid_date = (payment or {}).get("paidAt") or ""
    paid_date = paid_date[:10] if paid_date else "—"
    rows = [
        {
            "label": f"Frais d'inscription ({settings.platform_name})",
            "term": f"Année {year}-{year + 1}",
            "amount": round(float(inscription_amount), 2),
            "amountCdf": round(float(inscription_amount) * cdf_per_usd),
            "currency": "USD",
            "status": "Payé" if paid_inscription else "En attente",
            "date": paid_date if paid_inscription else "—",
            "source": "platform_inscription",
            "feeKey": "inscription",
        }
    ]
    categories = academic_fees.get("categories") if isinstance(academic_fees.get("categories"), dict) else {}
    if categories and academic_fees.get("useCategories", True):
        for defn in FEE_CATEGORY_DEFS:
            cat = categories.get(defn["key"]) or {}
            amt = float(cat.get("amount") or 0)
            if amt <= 0:
                continue
            rows.append(
                {
                    "label": cat.get("label") or defn["label"],
                    "term": defn["term"],
                    "amount": round(amt, 2),
                    "amountCdf": round(amt * cdf_per_usd),
                    "currency": "USD",
                    "status": "En attente",
                    "date": "—",
                    "source": "campus_academic",
                    "feeKey": defn["key"],
                    "categoryKey": defn["key"],
                }
            )
        return rows
    for key, label, term in (
        ("t1", "Frais académiques T1", "Trimestre 1"),
        ("t2", "Frais académiques T2", "Trimestre 2"),
        ("t3", "Frais académiques T3", "Trimestre 3"),
    ):
        amt = float(academic_fees.get(key, academic_fees["trimestre"])["amount"])
        rows.append(
            {
                "label": label,
                "term": term,
                "amount": amt,
                "amountCdf": round(amt * cdf_per_usd),
                "currency": "USD",
                "status": "En attente",
                "date": "—",
                "source": "campus_academic",
                "feeKey": key,
            }
        )
    return rows


def sync_academic_fees_to_students(universite_code: str, academic_fees: dict) -> int:
    if not universite_code or not academic_fees:
        return 0
    platform = get_platform_tariffs()
    insc_amount = platform["fees"]["etudiant"]["amount"]
    cdf = platform["cdfPerUsd"]
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    rows = db.execute(
        """SELECT id, payment, universite, sigle, code_uni FROM users
           WHERE role = 'etudiant'"""
    ).fetchall()
    updated = 0
    fees_json = json.dumps(academic_fees)
    for row in rows:
        if not _member_matches_university(row, universite_code):
            continue
        payment = None
        if row["payment"]:
            try:
                payment = json.loads(row["payment"])
            except json.JSONDecodeError:
                payment = None
        uni_fees = build_university_fees_for_student(
            academic_fees, payment, insc_amount, cdf
        )
        db.execute(
            """UPDATE users SET campus_academic_fees = ?, university_fees = ?, updated_at = ?
               WHERE id = ?""",
            (fees_json, json.dumps(uni_fees), now, row["id"]),
        )
        updated += 1
    db.commit()
    return updated


def update_university_academic_fees(user_id: str, body: dict) -> dict:
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    user = row_to_user(row)
    if not user or user.get("role") != "universite":
        raise ValueError("FORBIDDEN")
    academic = validate_academic_fees_payload(body)
    now = datetime.now(timezone.utc).isoformat()
    get_db().execute(
        "UPDATE users SET campus_academic_fees = ?, updated_at = ? WHERE id = ?",
        (json.dumps(academic), now, user_id),
    )
    get_db().commit()
    uni_code = user.get("universite") or user.get("sigle") or user.get("codeUni")
    members_updated = sync_academic_fees_to_students(uni_code, academic)
    return {
        "universite": uni_code,
        "academicFees": academic,
        "membersUpdated": members_updated,
    }


def normalize_platform_fee(role: str, raw: dict | None) -> dict:
    default = DEFAULT_PLATFORM_FEES.get(role, DEFAULT_PLATFORM_FEES["etudiant"])
    if not raw or not isinstance(raw, dict):
        return {**default}
    amount = float(raw.get("amount", 0))
    if not (0.5 <= amount <= 500):
        raise ValueError("INVALID_TARIFF_AMOUNT")
    return {
        "amount": round(amount, 2),
        "currency": "USD",
        "label": raw.get("label") or default["label"],
    }


def build_platform_tariffs_payload(raw: dict | None) -> dict:
    data = raw if isinstance(raw, dict) else {}
    fees_src = data.get("fees") if isinstance(data.get("fees"), dict) else {}
    fees = {}
    for role in PLATFORM_ROLES:
        fees[role] = normalize_platform_fee(role, fees_src.get(role))
    cdf = int(data.get("cdfPerUsd") or DEFAULT_CDF_PER_USD)
    if not (500 <= cdf <= 100000):
        raise ValueError("INVALID_EXCHANGE_RATE")
    return {
        "cdfPerUsd": cdf,
        "fees": fees,
        "updatedAt": data.get("updatedAt"),
        "updatedBy": data.get("updatedBy"),
    }


def get_platform_tariffs() -> dict:
    row = get_db().execute(
        "SELECT payload, updated_at, updated_by FROM platform_tariffs WHERE id = ?",
        ("default",),
    ).fetchone()
    if not row:
        return build_platform_tariffs_payload({})
    try:
        stored = json.loads(row["payload"]) if row["payload"] else {}
    except json.JSONDecodeError:
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    if row["updated_at"]:
        stored["updatedAt"] = row["updated_at"]
    if row["updated_by"]:
        stored["updatedBy"] = row["updated_by"]
    return build_platform_tariffs_payload(stored)


def update_platform_tariffs(user: dict, body: dict) -> dict:
    current = get_platform_tariffs()
    cdf = body.get("cdfPerUsd", current["cdfPerUsd"])
    if cdf is not None:
        cdf = int(cdf)
        if not (500 <= cdf <= 100000):
            raise ValueError("INVALID_EXCHANGE_RATE")
    else:
        cdf = current["cdfPerUsd"]

    fees_in = body.get("fees") or {}
    next_fees = dict(current["fees"])
    for role in PLATFORM_ROLES:
        if fees_in.get(role) is not None:
            next_fees[role] = normalize_platform_fee(role, fees_in[role])

    now = datetime.now(timezone.utc).isoformat()
    updated_by = user.get("email") or user.get("id")
    payload = {
        "cdfPerUsd": cdf,
        "fees": next_fees,
        "updatedAt": now,
        "updatedBy": updated_by,
    }
    db = get_db()
    existing = db.execute(
        "SELECT id FROM platform_tariffs WHERE id = ?", ("default",)
    ).fetchone()
    if existing:
        db.execute(
            "UPDATE platform_tariffs SET payload = ?, updated_at = ?, updated_by = ? WHERE id = ?",
            (json.dumps(payload), now, updated_by, "default"),
        )
    else:
        db.execute(
            "INSERT INTO platform_tariffs (id, payload, updated_at, updated_by) VALUES (?, ?, ?, ?)",
            ("default", json.dumps(payload), now, updated_by),
        )
    db.commit()
    return build_platform_tariffs_payload(payload)

