# Jarvis — Telegram Delivery Spec

> Standalone spec for the Telegram bot (Phase 3 of `DEV_SPEC.md` — the MVP delivery channel).
> Covers the 4 message types, their triggers, format, and rendering approach.
>
> **Core principle**: Telegram is push-native. Each message must be self-contained (no hover/drill-down). Warnings are text (impact); reviews carry small charts (rendered server-side as images). Deep analysis defers to web via a button.

---

## 0. Design constraints (why Telegram differs from web)

| Web (interactive) | Telegram (static) |
|-------------------|-------------------|
| Hover for values | None — bake values into the message |
| Click to drill down | Inline buttons as separate messages |
| Large screen, mouse | Small phone screen, thumb |
| Multiple tabs | Each message must stand alone |

**Do NOT shrink web charts into Telegram.** Redesign for the channel: keep the information, change the form. Radar charts don't read on a phone — use bars. Big equity curves become mini sparklines. The text conclusion is the hero; the chart is support.

---

## 1. Message types (priority order)

The 4 messages follow the user's daily rhythm:
```
During trade  → Real-time warning   (text, NO chart, immediate)
After close   → Mistake-notebook tag (buttons, lightweight)
End of day    → Daily summary        (mini chart + one-line conclusion)
Weekend       → Weekly mirror        (bar comparison + web hand-off)
```

---

### 1.1 Real-time warning (HIGHEST PRIORITY)

**Trigger**: Worker detects risk during an open position — fires from `jarvis_assess.assess()` when `coaching.triggered` or `guardrail.triggered`.

**Format**: Pure text. NO chart. A person mid-trade on their phone needs a clear sentence, not a graph.

**Two sub-types** (can fire together):

**(a) Impulse coaching** — left border warning color
```
⚠️ 잠깐 — 멈추는 걸 권합니다

연패 3회 직후, 평소보다 2.8배 큰 비중으로 ETHUSDT 롱에
들어갔습니다.

당신의 기록상 이 패턴(연패 후 몰빵)의 승률은 26%입니다.
평상시는 57%고요.
```

**(b) Damage guardrail** — left border danger color
```
🛑 피해 한도 경고

지금 손절이 없습니다. 청산되면 자본의 34%가 사라질 수 있어요.

청산 확률은 아무도 모릅니다. 하지만 이 손실 크기는 회복이
어렵습니다 (−34% 복구하려면 +52% 필요).
```

**Rules**:
- Always ground the warning in the user's OWN data ("your win rate 26%") and compound math ("+52% to recover"), never generic "this is risky."
- Anti-spam: respect the `rule_engine.py` hysteresis — alert once when violated, stay silent while ongoing, clear when resolved.
- Source data: `JarvisAssessment` (score, band, guardrail, coaching, headline).

---

### 1.2 Mistake-notebook tagging (2nd PRIORITY)

**Trigger**: Worker detects a position closed (`stoploss_tracker` reconcile / position qty → 0).

**Format**: A short prompt + inline button rows. Typing-free. Tap once.

```
ETHUSDT 롱 종료 · −4.2%
한 가지만 — 왜 들어갔었나요? (탭 한 번)

[진입 근거]
[기술적 셋업] [지지/저항] [추세]
[복수매매] [충동] [FOMO]        ← these 3 in warning color

[그때 감정은]
[평온] [확신] [조급] [분노/복수] [과욕]
```

**Rules**:
- Two button groups: rationale (`BaseRationale`) and emotion (`EmotionalState`). Map to `journal.py` enums.
- "Bad" rationales (impulse/revenge/fomo) styled in warning color so they're honest to tap, not hidden.
- Do NOT ask what the system already knows: stop-loss adherence (`stoploss_tracker`) and result (fills) are auto-filled.
- Optional one-liner: user can just reply to the message → saved as `JournalEntry.note`.
- Writes a `JournalEntry` row.

**Why it matters**: tagging "revenge" right after ignoring the 1.1 warning closes the loop — warning (before) → result (after) → self-tag (awareness). The act of tagging is itself the discipline training.

---

### 1.3 Daily summary (3rd PRIORITY)

**Trigger**: Scheduled, end of day (e.g. 23:00 user-local).

**Format**: Metric chips + mini equity sparkline (server-rendered image) + one-line conclusion.

```
📔 오늘의 복기 · 1월 14일

[거래 5건]  [승률 40%]  [손익 −6.1%]

<mini equity curve image — today's intraday capital>

오늘 5건 중 3건이 연패 후 충동 진입이었습니다. 그 3건에서만
−7.8%. 나머지 2건(기술적 셋업)은 +1.7%로 괜찮았어요.

기법이 문제가 아니었습니다. 충동 거래만 뺐으면 오늘은
플러스였습니다.
```

**Rules**:
- Chart is SUPPORT, not hero. The sentence ("if you'd skipped the impulse trades, today was green") is the point.
- Render chart server-side (matplotlib or Pillow) as PNG, send as photo with caption.
- Source: `viz_contract.to_situation_breakdown()` + `to_timeseries()` (single-day slice).
- Conclusion logic: compare disciplined vs impulsive subset P&L (from `journal.py` discipline_split).

---

### 1.4 Weekly mirror (4th PRIORITY)

**Trigger**: Scheduled, weekend (e.g. Sunday evening).

**Format**: Bar comparison (last week → this week), NOT radar. Radar doesn't read on small screens. Plus a web hand-off button.

```
🪞 이번 주 거울
지난주보다 무너지고 있습니다

지난주 → 이번주
승률        58% → 40%   ▓▓▓▓░░  (overlay bars)
손절 준수    77% → 27%   ▓▓░░░░
평균 레버리지 8x → 18x    ░░▓▓▓▓  (longer = worse)

세 지표 모두 나빠졌습니다. 특히 손절 준수가 77%→27%로
급락했어요. 기법이 아니라 손절 실행 — 심법이 흔들리고
있습니다.

[웹에서 자세히 보기 →]
```

**Rules**:
- Translate the web radar's "shrinking area" intuition into bars: grey (last week) under colored (this week). Shorter colored bar = worse for win-rate/SL; longer = worse for leverage.
- Render as server-side image OR as text bars (▓░) if simpler for MVP.
- Telegram only summarizes + triggers; deep per-trade drill-down defers to web ("웹에서 자세히 보기" button → deep link).
- Source: `viz_contract.to_journal_comparison()` (prev vs curr Baseline).

---

## 2. Channel division (don't violate)

- **Telegram = push**: warns when risky, prompts tagging, nudges with summaries.
- **Web = pull**: where users review deeply (interactive charts, per-trade history).
- Telegram summarizes and hands off; it does NOT try to be the full dashboard. This restraint keeps each channel strong.

---

## 3. Implementation notes

- **Warnings + tagging**: plain text + inline keyboards (native Telegram, easy).
- **Charts**: render with `matplotlib` or `Pillow` server-side → send as photo. The mockup mini-curve and overlay-bars are the design guide.
- **All data comes from `viz_contract.py` JSON** — same data the web renders interactively; Telegram just draws it as static images. Do not write a separate analysis path for Telegram.
- **Bot library**: `python-telegram-bot` (async) pairs naturally with the existing async `exchange.py`.
- **Localization**: all user-facing strings via `app/i18n.py` `t()` — add to both `locales/ko.json` and `locales/en.json`. The strings above are illustrative; final copy lives in locale files.
- **Worker integration**: these messages are emitted from the Phase 3 worker loop (see `DEV_SPEC.md` §3 Phase 3). Warning/tagging are event-driven (per snapshot / per close); summary/mirror are scheduled jobs.

---

*This spec = the Telegram delivery layer for the MVP. Backend analysis is already built and verified; this is purely the delivery channel on top of it.*
