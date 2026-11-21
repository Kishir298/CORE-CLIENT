"""Portal serialization: redaction + stable API schemas.

Mirrors ``core/portal/models.py`` semantics without importing C.O.R.E.
code (this package stays stdlib-only with no CORE-HOST imports).
"""

from __future__ import annotations

from typing import Any

_SECRET_HINTS = (
    "token",
    "credential",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "secret",
    "private_key",
    "privatekey",
    "s3_secret",
    "access_key",
    "session_token",
    "session-token",
)

_URL_KEYS = ("database_url", "db_url", "connection_string", "dsn", "endpoint_url")


def _looks_secret(name: str) -> bool:
    """Boundary-aware secret check (avoids false positives).

    Matches whole names (``api_key``) or ``_``/``-``-suffixed forms
    (``session_token``), so display markers like ``session_token_state``
    and data fields like ``key`` survive redaction.
    """
    lowered = str(name).lower()
    if lowered in _SECRET_HINTS:
        return True
    return any(
        lowered.endswith("_" + hint) or lowered.endswith("-" + hint)
        for hint in _SECRET_HINTS
    )


def _scrub_url(value: Any) -> Any:
    if not isinstance(value, str) or "://" not in value:
        return value
    try:
        scheme, _, rest = value.partition("://")
        authority, _, _path = rest.partition("/")
        if "@" in authority:
            _userinfo, _, host = authority.partition("@")
            return f"{scheme}://***@{host}"
        return value
    except Exception:
        return "***redacted***"


def redact(value: Any) -> Any:
    """Recursively remove/mask secret material from a JSON-safe structure."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if _looks_secret(key):
                continue
            if str(key).lower() in _URL_KEYS:
                cleaned[key] = _scrub_url(item)
            else:
                cleaned[key] = redact(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def envelope(data: Any, *, ok: bool = True, error: str | None = None) -> dict:
    """Stable portal response envelope."""
    body: dict[str, Any] = {"ok": ok, "data": redact(data)}
    if error:
        body["error"] = error
    return body


HOST_OFFLINE = {"online": False, "detail": "C.O.R.E. HOST OFFLINE"}


def session_view(summary: dict) -> dict:
    """Public session shape: state flags, never secret values."""
    has_token = bool(summary.get("session_token"))
    view = {
        "device": summary.get("device"),
        "device_id": summary.get("device_id"),
        "join_name": summary.get("join_name"),
        "status": summary.get("status"),
        "authenticated": summary.get("status") not in (None, "DISCONNECTED"),
        "connection_id": summary.get("connection_id"),
        "session_note": "RAM ONLY — never persisted",
        "lease_duration_seconds": summary.get("lease_duration_seconds"),
        "connected_at": summary.get("connected_at"),
        "lease_expires_at": summary.get("lease_expires_at"),
        "lease_remaining": summary.get("lease_remaining"),
    }
    cleaned = redact(view)
    # Named *_state (not *token) so envelope redaction can never strip it;
    # the value is only ever "ACTIVE" or None — never the secret itself.
    cleaned["session_token_state"] = "ACTIVE" if has_token else None
    return cleaned
