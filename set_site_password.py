#!/usr/bin/env python3
import argparse
import hashlib
import secrets
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env.local"
ITERATIONS = 200_000


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


def main():
    parser = argparse.ArgumentParser(description="Set site password hash into .env.local")
    parser.add_argument("--password", required=True, help="Plaintext password to hash")
    args = parser.parse_args()

    salt = secrets.token_hex(16)
    password_hash = hash_password(args.password, salt)

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    lines = upsert(lines, "SITE_PASSWORD_SALT", salt)
    lines = upsert(lines, "SITE_PASSWORD_HASH", password_hash)
    lines = upsert(lines, "SITE_SESSION_SECRET", secrets.token_hex(32))
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"updated {ENV_PATH}")


if __name__ == "__main__":
    main()
