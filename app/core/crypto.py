"""Symmetric encryption for stored integration credentials (Fernet)."""
import base64
import hashlib
import json
import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

_KEY_FILE = Path(__file__).resolve().parents[2] / ".secret.key"


def _fernet():
    from cryptography.fernet import Fernet

    if settings.encryption_key:
        # Accept any string: derive a urlsafe 32-byte key from it
        digest = hashlib.sha256(settings.encryption_key.encode()).digest()
        return Fernet(base64.urlsafe_b64encode(digest))

    # No key configured: generate one once and persist locally
    if _KEY_FILE.exists():
        key = _KEY_FILE.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        _KEY_FILE.write_bytes(key)
        logger.info(f"Generated local encryption key at {_KEY_FILE}")
    return Fernet(key)


def encrypt_dict(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_dict(token: str) -> dict:
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(token.encode()).decode())
    except Exception:
        return {}
