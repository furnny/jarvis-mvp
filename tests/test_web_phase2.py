"""
Phase 2 web tests — the 5 acceptance criteria, end to end on sqlite.

No live exchange/email: the email sender is the console sender (captures the
link), and the key permission fetcher is overridden with a fake.
"""
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient

from app.web import key_validation
from app.web.key_validation import KeyPermissions


@pytest.fixture
def client(app_db):
    from app.web.main import app
    return TestClient(app)


def _fake_perms(**over):
    base = dict(enable_withdrawals=False, enable_internal_transfer=False,
                enable_spot_margin_trading=False, enable_margin=False,
                enable_futures=True, ip_restricted=True)
    base.update(over)
    return KeyPermissions(**base)


# ── 1) magic-link flow end to end ────────────────────────────────────────────

def test_magic_link_flow_end_to_end(client):
    from app.web.email import ConsoleEmailSender

    r = client.post("/auth/request-link", json={"email": "trader@example.com"})
    assert r.status_code == 200 and r.json()["sent"] is True
    # response must NOT leak the token
    assert "token" not in r.text and "verify?token" not in r.text

    link = ConsoleEmailSender.last_link
    assert link and "/auth/verify?token=" in link
    token = link.split("token=")[1]

    # click the link → authenticated session cookie set
    r2 = client.get(f"/auth/verify?token={token}")
    assert r2.status_code == 200 and r2.json()["authenticated"] is True
    assert "jarvis_session" in r2.cookies or "jarvis_session" in client.cookies

    # authenticated request now works (link-code requires auth)
    r3 = client.post("/api/telegram/link-code")
    assert r3.status_code == 200

    # token is one-time use
    assert client.get(f"/auth/verify?token={token}").status_code == 400


def test_unauthenticated_is_rejected(client):
    assert client.post("/api/keys", json={
        "api_key": "x" * 10, "api_secret": "y" * 10}).status_code == 401


# ── 2) key input HARD-rejects withdraw/trade perms ───────────────────────────

def _login(client, email="t@example.com"):
    from app.web.email import ConsoleEmailSender
    client.post("/auth/request-link", json={"email": email})
    token = ConsoleEmailSender.last_link.split("token=")[1]
    client.get(f"/auth/verify?token={token}")


def test_key_with_withdrawals_is_rejected(client, monkeypatch):
    _login(client)
    key_validation.set_permission_fetcher(
        lambda k, s: _async(_fake_perms(enable_withdrawals=True))
    )
    try:
        r = client.post("/api/keys", json={"api_key": "k" * 10, "api_secret": "s" * 10})
        assert r.status_code == 400
        assert "withdraw" in r.json()["detail"].lower() or "출금" in r.json()["detail"]
    finally:
        key_validation.set_permission_fetcher(None)


def test_key_with_spot_trading_is_rejected(client):
    _login(client)
    key_validation.set_permission_fetcher(
        lambda k, s: _async(_fake_perms(enable_spot_margin_trading=True))
    )
    try:
        r = client.post("/api/keys", json={"api_key": "k" * 10, "api_secret": "s" * 10})
        assert r.status_code == 400
    finally:
        key_validation.set_permission_fetcher(None)


def test_read_only_key_is_accepted_and_encrypted(client, app_db):
    import asyncio
    from sqlalchemy import select
    from app.db import models

    _login(client, email="ro@example.com")
    key_validation.set_permission_fetcher(lambda k, s: _async(_fake_perms()))
    try:
        r = client.post("/api/keys", json={
            "api_key": "REALKEY123", "api_secret": "REALSECRET456"})
        assert r.status_code == 200 and r.json()["accepted"] is True
    finally:
        key_validation.set_permission_fetcher(None)

    # 3) stored ciphertext must NOT contain plaintext key/secret
    async def _check():
        async with app_db() as s:
            row = (await s.execute(select(models.ApiCredential))).scalar_one()
            assert "REALKEY123" not in row.ciphertext
            assert "REALSECRET456" not in row.ciphertext
            assert row.permissions == ["read"]
    asyncio.get_event_loop().run_until_complete(_check())


# ── 4) diagnostic copy via i18n, both locales ────────────────────────────────

def test_diagnostic_questions_localized(client):
    en = client.get("/api/diagnostic/questions?lang=en").json()
    ko = client.get("/api/diagnostic/questions?lang=ko").json()
    assert len(en["questions"]) == 5 and len(ko["questions"]) == 5
    # real translations, not raw keys
    assert all(q["text"] and not q["text"].startswith("diagnostic.") for q in en["questions"])
    assert en["questions"][0]["text"] != ko["questions"][0]["text"]


def test_diagnostic_classifies_discipline_deficient(client):
    # strong technique, weak discipline → mind_deficient
    answers = {"has_edge": "often", "plan_exit": "often",
               "cut_losses": "rarely", "revenge": "often", "size_control": "rarely"}
    r = client.post("/api/diagnostic/submit?lang=en", json={"answers": answers})
    body = r.json()
    assert body["type"] == "mind_deficient"
    assert body["headline"] and not body["headline"].startswith("diagnostic.")


# ── 5) telegram chat_id linking flow ─────────────────────────────────────────

def test_telegram_link_flow(client, app_db):
    import asyncio
    from sqlalchemy import select
    from app.db import models
    from app.web.services import consume_telegram_link_code

    _login(client, email="tg@example.com")
    r = client.post("/api/telegram/link-code")
    assert r.status_code == 200
    code = r.json()["code"]
    assert r.json()["deep_link"].endswith(code)

    # bot side consumes the code → sets telegram_chat_id
    async def _go():
        ok = await consume_telegram_link_code(code, chat_id=555123)
        assert ok is True
        async with app_db() as s:
            user = (await s.execute(
                select(models.User).where(models.User.email == "tg@example.com")
            )).scalar_one()
            assert user.telegram_chat_id == 555123
        # one-time use
        assert await consume_telegram_link_code(code, chat_id=999) is False
    asyncio.get_event_loop().run_until_complete(_go())


# helper: wrap a value in a coroutine for the fetcher seam
def _async(value):
    async def _c():
        return value
    return _c()
