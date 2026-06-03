"""Jarvis Telegram delivery layer (Phase 3).

Telegram is the PUSH channel for the MVP:
  - real-time warnings (text, grounded in the user's own data)
  - mistake-notebook tagging (inline keyboards → JournalEntry)
  - daily summary (sparkline image + one-line conclusion)
  - weekly mirror (bar comparison image + web hand-off)

Hard rules (see TELEGRAM_SPEC.md):
  - All message DATA comes from viz_contract / jarvis_assess — no separate
    analysis path.
  - Warnings are anti-spam gated by rule_engine hysteresis.
  - Every user-facing string goes through app.i18n.t().
  - Charts render server-side as PNG and ship as photos.
"""
