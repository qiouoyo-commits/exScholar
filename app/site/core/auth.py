"""Authentication helpers for exScholar."""

from .base import *


def _read_registry_file(default):
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_registry_file(payload: dict):
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = USERS_FILE.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(USERS_FILE)


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _load_user_registry() -> dict:
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    if not USERS_FILE.exists():
        return {"users": []}
    payload = _read_registry_file({"users": []})
    if not isinstance(payload, dict):
        return {"users": []}
    users = payload.get("users")
    if not isinstance(users, list):
        payload["users"] = []
    return payload


def _save_user_registry(payload: dict):
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    _write_registry_file(payload)


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    ).hex()


def verify_password(password: str, salt: str, password_hash: str) -> bool:
    if not salt or not password_hash:
        return False
    candidate = hash_password(password, salt)
    return hmac.compare_digest(candidate, password_hash)


def list_users() -> list[dict]:
    payload = _load_user_registry()
    users = []
    for item in payload.get("users") or []:
        if not isinstance(item, dict):
            continue
        username = sanitize_username(item.get("username") or "")
        if not username:
            continue
        users.append(
            {
                "username": username,
                "password_salt": str(item.get("password_salt") or ""),
                "password_hash": str(item.get("password_hash") or ""),
                "created_at": str(item.get("created_at") or ""),
            }
        )
    return users


def get_user(username: str) -> dict | None:
    normalized = sanitize_username(username)
    if not normalized:
        return None
    for item in list_users():
        if item["username"] == normalized:
            return item
    return None


def has_users() -> bool:
    return bool(list_users())


def create_user(username: str, password: str) -> dict:
    normalized = sanitize_username(username)
    password_text = str(password or "")
    if not normalized:
        raise ValueError("username 不合法，只能包含字母、数字、- 和 _")
    if len(password_text) < 4:
        raise ValueError("password 至少需要 4 个字符")
    payload = _load_user_registry()
    for item in payload.get("users") or []:
        if sanitize_username(item.get("username") or "") == normalized:
            raise ValueError("username 已存在")
    salt = secrets.token_hex(16)
    record = {
        "username": normalized,
        "password_salt": salt,
        "password_hash": hash_password(password_text, salt),
        "created_at": _utc_now(),
    }
    payload.setdefault("users", []).append(record)
    _save_user_registry(payload)
    ensure_user_data_dirs(normalized)
    return record


def _ensure_legacy_admin_user(password: str, username: str) -> dict | None:
    normalized = sanitize_username(username)
    if normalized != "admin":
        return None
    if not PASSWORD_SALT or not PASSWORD_HASH:
        return None
    existing = get_user(normalized)
    if existing:
        return existing if verify_password(password, existing.get("password_salt") or "", existing.get("password_hash") or "") else None
    if not verify_password(password, PASSWORD_SALT, PASSWORD_HASH):
        return None
    payload = _load_user_registry()
    record = {
        "username": normalized,
        "password_salt": PASSWORD_SALT,
        "password_hash": PASSWORD_HASH,
        "created_at": _utc_now(),
    }
    payload.setdefault("users", []).append(record)
    _save_user_registry(payload)
    ensure_user_data_dirs(normalized)
    return record


def authenticate_user(username: str, password: str) -> dict | None:
    normalized = sanitize_username(username)
    user = get_user(normalized)
    if user and verify_password(password, user.get("password_salt") or "", user.get("password_hash") or ""):
        ensure_user_data_dirs(normalized)
        return user
    legacy = _ensure_legacy_admin_user(password, normalized)
    if legacy:
        return legacy
    return None


def require_password() -> bool:
    return has_users() or bool(PASSWORD_SALT and PASSWORD_HASH)
