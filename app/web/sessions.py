"""
Stateless signed session cookies (magic-link auth has no password).

A session is a short signed token carrying the user id + expiry. Signed with
SESSION_SECRET via itsdangerous; tamper or expiry → invalid.
"""
from __future__ import annotations
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings

COOKIE_NAME = "jarvis_session"
_SALT = "jarvis-session-v1"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().SESSION_SECRET, salt=_SALT)


def issue_session(user_id: int) -> str:
    return _serializer().dumps({"uid": user_id})


def read_session(token: str) -> int | None:
    """Return user_id if the token is valid and unexpired, else None."""
    max_age = get_settings().SESSION_TTL_HOURS * 3600
    try:
        data = _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    uid = data.get("uid")
    return int(uid) if uid is not None else None
