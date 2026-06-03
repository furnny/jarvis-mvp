"""FastAPI dependencies — current user from the signed session cookie."""
from __future__ import annotations
from fastapi import Cookie, HTTPException, status

from app.web.sessions import COOKIE_NAME, read_session


async def current_user_id(jarvis_session: str | None = Cookie(default=None)) -> int:
    """Resolve the authenticated user id, or 401."""
    if not jarvis_session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    uid = read_session(jarvis_session)
    if uid is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired session")
    return uid


# re-export for convenience
__all__ = ["current_user_id", "COOKIE_NAME"]
