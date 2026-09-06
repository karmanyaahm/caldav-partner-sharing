"""Recipient tokens. The token is the object key, so it must be unguessable."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from .config import tokens_path

TOKEN_BYTES = 24  # 192 bits, base64url -> 32 chars


def mint() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def load() -> dict[str, dict]:
    path = tokens_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save(tokens: dict[str, dict]) -> None:
    path = tokens_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)


def add(label: str) -> tuple[str, dict]:
    tokens = load()
    token = mint()
    entry = {
        "label": label,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "revoked": None,
    }
    tokens[token] = entry
    save(tokens)
    return token, entry


def mark_revoked(token: str) -> None:
    tokens = load()
    if token in tokens:
        tokens[token]["revoked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save(tokens)


def forget(token: str) -> None:
    tokens = load()
    tokens.pop(token, None)
    save(tokens)


def active(tokens: dict[str, dict] | None = None) -> list[str]:
    tokens = load() if tokens is None else tokens
    return [t for t, meta in tokens.items() if not meta.get("revoked")]
