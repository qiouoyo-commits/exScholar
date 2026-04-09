#!/home/ubuntu/miniconda3/envs/openclaw-analytics/bin/python
import argparse
import hashlib
import json
import re
import secrets
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env.local"
ITERATIONS = 200_000
DATA_DIR = ROOT_DIR / "data"
USERS_DIR = DATA_DIR / "users"
USERS_FILE = USERS_DIR / "users.json"


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), ITERATIONS
    ).hex()


def upsert(lines: list[str], key: str, value: str) -> list[str]:
    prefix = f"{key}="
    for idx, line in enumerate(lines):
        if line.startswith(prefix):
            lines[idx] = f"{prefix}{value}"
            return lines
    lines.append(f"{prefix}{value}")
    return lines


def sanitize_username(username: str) -> str:
    value = (username or "").strip().lower()
    value = re.sub(r"[^a-z0-9_-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-_")
    return value[:48]


def load_users() -> dict:
    if not USERS_FILE.exists():
        return {"users": []}
    try:
        payload = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"users": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("users"), list):
        return {"users": []}
    return payload


def save_users(payload: dict):
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_user_dirs(username: str):
    user_root = USERS_DIR / username
    for name in ("searches", "reading", "library", "expansions", "openclaw_jobs", "research_jobs"):
        (user_root / name).mkdir(parents=True, exist_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Create or update an exScholar user account")
    parser.add_argument("--username", required=True, help="Username for the account")
    parser.add_argument("--password", required=True, help="Plaintext password to hash")
    args = parser.parse_args()

    username = sanitize_username(args.username)
    if not username:
        raise SystemExit("invalid username")
    salt = secrets.token_hex(16)
    password_hash = hash_password(args.password, salt)

    payload = load_users()
    users = payload.setdefault("users", [])
    updated = False
    for item in users:
        if sanitize_username(item.get("username") or "") == username:
            item["username"] = username
            item["password_salt"] = salt
            item["password_hash"] = password_hash
            updated = True
            break
    if not updated:
        users.append(
            {
                "username": username,
                "password_salt": salt,
                "password_hash": password_hash,
            }
        )
    save_users(payload)
    ensure_user_dirs(username)

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    lines = upsert(lines, "SITE_SESSION_SECRET", secrets.token_hex(32))
    if username == "admin":
        lines = upsert(lines, "SITE_PASSWORD_SALT", salt)
        lines = upsert(lines, "SITE_PASSWORD_HASH", password_hash)
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"updated {USERS_FILE}")


if __name__ == "__main__":
    main()
