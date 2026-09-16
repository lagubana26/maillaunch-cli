"""
Secure local storage for OAuth tokens and client secrets.

Strategy:
  1. Prefer the OS keychain via `keyring` (macOS Keychain, Secret
     Service on Linux). This is the safest option and needs no key
     management on our part.
  2. If no OS keychain backend is available (e.g. headless Linux CI
     box with no Secret Service running), fall back to an encrypted
     file at ~/.maillaunch/credentials.enc, using a Fernet key that is
     itself stored via keyring if possible, or generated once and
     saved with 0600 permissions to ~/.maillaunch/.key as a last
     resort. Plaintext credentials are never written to disk.

Every value is namespaced by a service+account pair so Gmail and
Microsoft tokens (and multiple accounts) don't collide.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Optional

SERVICE_NAME = "maillaunch"
STORE_DIR = Path.home() / ".maillaunch"
FALLBACK_KEY_FILE = STORE_DIR / ".key"
FALLBACK_STORE_FILE = STORE_DIR / "credentials.enc"

try:
    import keyring
    from keyring.errors import KeyringError

    _KEYRING_AVAILABLE = True
except ImportError:  # pragma: no cover - keyring is a required dep,
    _KEYRING_AVAILABLE = False  # but we degrade gracefully if missing.

    class KeyringError(Exception):
        pass


def _ensure_store_dir() -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STORE_DIR, stat.S_IRWXU)  # 0700, owner only


def _get_fernet():
    from cryptography.fernet import Fernet

    key: Optional[bytes] = None
    if _KEYRING_AVAILABLE:
        try:
            existing = keyring.get_password(SERVICE_NAME, "_fallback_key")
            if existing:
                key = existing.encode()
        except KeyringError:
            key = None

    if key is None:
        _ensure_store_dir()
        if FALLBACK_KEY_FILE.exists():
            key = FALLBACK_KEY_FILE.read_bytes()
        else:
            key = Fernet.generate_key()
            if _KEYRING_AVAILABLE:
                try:
                    keyring.set_password(SERVICE_NAME, "_fallback_key", key.decode())
                except KeyringError:
                    pass
            FALLBACK_KEY_FILE.write_bytes(key)
            os.chmod(FALLBACK_KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0600

    return Fernet(key)


def _fallback_load() -> dict:
    if not FALLBACK_STORE_FILE.exists():
        return {}
    f = _get_fernet()
    decrypted = f.decrypt(FALLBACK_STORE_FILE.read_bytes())
    return json.loads(decrypted.decode())


def _fallback_save(data: dict) -> None:
    _ensure_store_dir()
    f = _get_fernet()
    encrypted = f.encrypt(json.dumps(data).encode())
    FALLBACK_STORE_FILE.write_bytes(encrypted)
    os.chmod(FALLBACK_STORE_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0600


def set_secret(account: str, value: Any) -> None:
    """Store a secret (dict, str, etc. -- JSON-serialized) for `account`."""
    payload = json.dumps(value)
    if _KEYRING_AVAILABLE:
        try:
            keyring.set_password(SERVICE_NAME, account, payload)
            return
        except KeyringError:
            pass
    data = _fallback_load()
    data[account] = value
    _fallback_save(data)


def get_secret(account: str) -> Optional[Any]:
    if _KEYRING_AVAILABLE:
        try:
            payload = keyring.get_password(SERVICE_NAME, account)
            if payload is not None:
                return json.loads(payload)
        except KeyringError:
            pass
    data = _fallback_load()
    return data.get(account)


def delete_secret(account: str) -> None:
    if _KEYRING_AVAILABLE:
        try:
            keyring.delete_password(SERVICE_NAME, account)
        except KeyringError:
            pass
    data = _fallback_load()
    if account in data:
        del data[account]
        _fallback_save(data)
