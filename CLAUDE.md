# CLAUDE.md — Jarvis

> Instructions for Claude Code. Read this first, then `DEV_SPEC.md`.

## What this is

Jarvis is a **survival-first risk + discipline tool** for crypto futures traders. It is NOT a signal bot or auto-trader. It reads a trader's history (read-only API) and shows them, with their own data, *where their discipline breaks* — so they survive long enough for opportunity to arrive.

Core thesis: **"You already have the technique. Jarvis shows you the moment your discipline breaks."** Most tools analyze the *market*; Jarvis analyzes *the trader*. That's the moat.

## Current state

- **17 core modules (~3,800 lines) are written and self-verified.** Each has a `__main__` block — run `python3 app/<path>.py` to see it work.
- The analysis logic, risk math, personalization, journaling, and backtest are **done and validated**.
- **What's NOT built yet** (this is your job): DB persistence, web server, auth/billing, the background worker loop, and the frontend. See `DEV_SPEC.md` §3.

## Ground rules (do not violate)

1. **Read-only keys only.** Never request or use trade/withdraw permissions. No auto-ordering in v1.
2. **Never show a wrong number.** All financial math uses `Decimal`. If something can't be measured, say so — don't fake it.
3. **Don't score what you can't measure.** Survival/behavior axes are deliberately kept OUT of the score (they failed backtest prediction) — they live as a damage *guardrail* and *coaching* instead. Don't "fix" this by folding them back into the score.
4. **Insufficient sample → withhold judgment.** `MIN_RELIABLE_SAMPLE = 8`. Don't report stats below it.
5. **Evaluate against the user's own baseline,** not absolute thresholds.
6. **Advisor, not agent.** Information and analysis only — never "buy/sell" instructions.
7. **Encrypt keys** with `crypto.py` envelope encryption; decrypt only in worker memory.

## i18n — already set up, follow the pattern

- User-facing strings live in `locales/ko.json` and `locales/en.json` (key-for-key mirror).
- Use `from app.i18n import t, set_locale` then `t("section.key", **kwargs)`.
- **When you add any user-facing string, add it to BOTH locale files** — never hardcode Korean or English in module logic.
- The existing modules still have inline Korean strings in their logic (in the f-strings that build messages). **Migrating those to `t()` calls is a task** — the locale files already contain the translations, so it's wiring, not translation. Verify each module's `__main__` still passes after migration.

## Build order

Follow `DEV_SPEC.md` §3 phases:
1. **Persistence** — implement the `*Store` Protocols (e.g. `StopLossStore` in `stoploss_tracker.py`) against Postgres. Schema is in DEV_SPEC §3.
2. **Real API** — `run_analysis.py` is the working entry point; it just needs a networked environment + env vars.
3. **Auth + billing** — Stripe tiers: Free / Pro / Elite.
4. **Worker** — wrap the existing modules in the loop sketched in DEV_SPEC §3 Phase 3.
5. **Frontend** — render the `viz_contract.py` JSON. Visual mockups exist (described in DEV_SPEC §4).

## Known issues to fix with real data

- `viz_contract.to_timeseries`: equity curve goes flat when trades cluster in one period; mark "no-trade weeks" distinctly in the UI.
- Breakpoint detection (timeseries) is over-sensitive — uses a simple 15%p drop; use "2 consecutive down-weeks" or trend slope instead.
- Diagnostic scoring thresholds (the 0.5 cutoffs) are hypotheses — calibrate against real connected-user data once available, by comparing self-diagnosis vs data-diagnosis.

## How to verify your work

Every module must keep passing its `__main__` self-test. The end-to-end check is `python3 demo_integration.py` — it runs the full pipeline on mock data. Keep it green.

## Tone of the product

Jarvis speaks like a calm staff officer (참모), not a cheerleader or a nag. It states facts from the user's own data — praises discipline when earned, warns against overconfidence when winning, and is blunt (not cruel) about losses. See the existing message strings in the locale files for the voice.
