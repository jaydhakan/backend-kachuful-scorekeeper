"""Random tokens. Invite, reset and session tokens are stored only as SHA-256."""

import hashlib
import secrets

TOKEN_BYTES = 32
SHARE_TOKEN_BYTES = 16  # 22 url-safe characters


def new_token() -> str:
    """32 random bytes, url-safe."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def new_share_token() -> str:
    """A 22-character url-safe share-link token (stored plain: the owner shows it as a QR)."""
    return secrets.token_urlsafe(SHARE_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Hex SHA-256: what the database stores and looks up by."""
    return hashlib.sha256(token.encode()).hexdigest()
