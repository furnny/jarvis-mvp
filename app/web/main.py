"""
Jarvis web app (Phase 2) — magic-link auth, read-only key input, landing
diagnostic, Telegram chat linking.

Endpoints:
  GET  /health
  POST /auth/request-link        {email}            → emails a magic link
  GET  /auth/verify?token=...                       → sets session cookie
  POST /auth/logout
  GET  /api/diagnostic/questions                    → localized 5 questions
  POST /api/diagnostic/submit    {answers, style?}  → tentative type
  POST /api/keys                 {api_key, api_secret, exchange?}  (auth)
  POST /api/telegram/link-code                       (auth) → code + deep link
"""
from __future__ import annotations
import logging

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field

from app.config import get_settings
from app.i18n import t
from app.web import services
from app.web.deps import current_user_id
from app.web.email import get_email_sender
from app.web.sessions import COOKIE_NAME, issue_session
from app.web.diagnostic import questions_payload, classify
from app.web.key_validation import (
    KeyValidationError, check_read_only, get_key_permissions,
)
from app.db.persistence import register_credential

log = logging.getLogger("jarvis.web")
app = FastAPI(title="Jarvis", version="0.2.0")


# ── schemas ──────────────────────────────────────────────────────────────────

class RequestLinkIn(BaseModel):
    email: EmailStr


class DiagnosticIn(BaseModel):
    answers: dict[str, str]
    style: str | None = None


class KeysIn(BaseModel):
    api_key: str = Field(min_length=8)
    api_secret: str = Field(min_length=8)
    exchange: str = "binance"


# ── health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


# ── auth (magic link) ────────────────────────────────────────────────────────

@app.post("/auth/request-link")
async def request_link(body: RequestLinkIn):
    raw = await services.create_magic_link_token(body.email)
    link = f"{get_settings().APP_BASE_URL}/auth/verify?token={raw}"
    await get_email_sender().send_magic_link(body.email, link)
    # Never return the token/link in the response — it goes only to email.
    return {"sent": True}


@app.get("/auth/verify")
async def verify(token: str, response: Response):
    uid = await services.consume_magic_link_token(token)
    if uid is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid or expired link")
    settings = get_settings()
    resp = JSONResponse({"authenticated": True, "user_id": uid})
    resp.set_cookie(
        COOKIE_NAME, issue_session(uid),
        max_age=settings.SESSION_TTL_HOURS * 3600,
        httponly=True, samesite="lax",
        secure=settings.APP_BASE_URL.startswith("https"),
    )
    return resp


@app.post("/auth/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ── landing diagnostic ───────────────────────────────────────────────────────

@app.get("/api/diagnostic/questions")
async def diagnostic_questions(lang: str | None = None):
    return questions_payload(lang)


@app.post("/api/diagnostic/submit")
async def diagnostic_submit(body: DiagnosticIn, lang: str | None = None):
    result = classify(body.answers, lang=lang)
    return {
        "type": result.type_key,
        "type_label": result.type_label,
        "headline": result.headline,
        "technique_score": result.technique_score,
        "discipline_score": result.discipline_score,
        "cta": t("diagnostic.cta.connect", lang=lang),
    }


# ── read-only API key input ──────────────────────────────────────────────────

@app.post("/api/keys")
async def add_keys(
    body: KeysIn, lang: str | None = None, user_id: int = Depends(current_user_id)
):
    # 1) verify permissions with the exchange (never log the secret)
    try:
        perms = await get_key_permissions(body.api_key, body.api_secret)
    except KeyValidationError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("keys.unreachable", lang=lang))

    # 2) HARD reject anything beyond read-only
    violations = check_read_only(perms)
    if violations:
        readable = ", ".join(t(f"keys.rejected.{v}", lang=lang) for v in violations)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            t("keys.rejected.reason", lang=lang, violations=readable),
        )

    # 3) encrypt-at-rest via CredentialVault, then store (plaintext never persisted)
    await register_credential(
        user_id, body.api_key, body.api_secret,
        exchange=body.exchange, permissions=["read"],
    )
    return {"accepted": True, "message": t("keys.accepted", lang=lang)}


# ── telegram chat linking ────────────────────────────────────────────────────

@app.post("/api/telegram/link-code")
async def telegram_link_code(
    lang: str | None = None, user_id: int = Depends(current_user_id)
):
    code = await services.create_telegram_link_code(user_id)
    bot = get_settings().TELEGRAM_BOT_USERNAME
    return {
        "code": code,
        "deep_link": f"https://t.me/{bot}?start={code}",
        "instructions": t("telegram.link.instructions", lang=lang, code=code),
    }
