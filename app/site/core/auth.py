"""Authentication helpers for exScholar."""

from .base import *


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    ).hex()


def verify_password(password: str) -> bool:
    if not PASSWORD_SALT or not PASSWORD_HASH:
        return False
    candidate = hash_password(password, PASSWORD_SALT)
    return hmac.compare_digest(candidate, PASSWORD_HASH)


def require_password() -> bool:
    return bool(PASSWORD_SALT and PASSWORD_HASH)
