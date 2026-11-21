"""Хеширование паролей (PBKDF2-SHA256 с солью)."""
import hashlib
import hmac
import os


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)


def hash_password(password: str):
    """Возвращает (соль, хеш) в виде hex-строк."""
    salt = os.urandom(16)
    return salt.hex(), _derive(password, salt).hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    return hmac.compare_digest(_derive(password, bytes.fromhex(salt_hex)).hex(), hash_hex)