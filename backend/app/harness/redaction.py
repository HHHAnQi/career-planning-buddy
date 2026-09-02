"""Recursive redaction and stable hashing for developer-facing trace data."""

import json
from hashlib import sha256
from typing import Final

SENSITIVE_FRAGMENTS: Final[tuple[str, ...]] = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "jwt",
    "password",
    "secret",
    "token",
)
REDACTED: Final[str] = "[REDACTED]"


def redact(value: object, *, key: str | None = None) -> object:
    """Return a JSON-compatible copy with sensitive values removed."""
    if key is not None and any(fragment in key.lower() for fragment in SENSITIVE_FRAGMENTS):
        return REDACTED
    if isinstance(value, dict):
        return {str(item_key): redact(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    return value


def stable_hash(value: object) -> str:
    """Hash canonical JSON without logging or returning the original value."""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def redacted_snapshot(value: object) -> tuple[object, str]:
    """Return a redacted snapshot and the hash of the unredacted snapshot."""
    return redact(value), stable_hash(value)
