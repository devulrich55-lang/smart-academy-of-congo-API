import json

from datetime import datetime, timezone



from app.database import get_db, row_to_user



DEFAULT_CDF_PER_USD = 2800



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

    return {

        "universite": universite_code,

        "universityName": uni.get("nomUniversite") if uni else None,

        "configured": bool(custom),

        "tariffs": merged,

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

