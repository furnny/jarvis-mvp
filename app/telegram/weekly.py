"""
Weekly mirror (TELEGRAM_SPEC §1.4) — 4th priority.

Bar comparison (last week → this week), NOT radar (radar doesn't read on a
phone). Plus a "see details on the web" hand-off button — Telegram summarizes,
the web does the deep per-trade drill-down.

Data source: viz_contract.to_journal_comparison(prev, curr, style). We draw
three axes the user feels weekly — win rate, stop-loss adherence, avg leverage —
from the radar's normalized values, and pull raw numbers from the Baselines for
the grounded conclusion.
"""
from __future__ import annotations
from typing import Optional

from app.core.baseline import Baseline
from app.i18n import t
from app.telegram import charts
from app.telegram.notifier import Button

# Radar axis order in viz_contract.to_journal_comparison:
#   0 win rate, 1 profitability, 2 SL coverage, 3 leverage restraint, 4 consistency
# We surface the three the user feels weekly. "worse" = this-week normalized
# value LOWER than last week (all three normalized so higher = better).
_AXES = [
    (0, "telegram.weekly.metric.winrate"),
    (2, "telegram.weekly.metric.sl"),
    (3, "telegram.weekly.metric.leverage"),
]


def _subhead(comparison: dict, *, lang: Optional[str]) -> str:
    tone = comparison.get("verdict", {}).get("tone", "neutral")
    if tone == "danger":
        return t("telegram.weekly.subhead.worse", lang=lang)
    if tone == "success":
        return t("telegram.weekly.subhead.better", lang=lang)
    return t("telegram.weekly.subhead.mixed", lang=lang)


def _conclusion(prev: Baseline, curr: Baseline, *, lang: Optional[str]) -> str:
    """Lead with the biggest discipline regression we can ground in numbers.
    Stop-loss adherence is the headline signal (technique vs 심법)."""
    prev_sl = round(prev.sl_coverage * 100)
    curr_sl = round(curr.sl_coverage * 100)
    if prev_sl - curr_sl >= 15:  # meaningful drop in stop-loss discipline
        return t("telegram.weekly.conclusion.sl_drop", lang=lang,
                 prev=prev_sl, curr=curr_sl)
    return t("telegram.weekly.conclusion.generic", lang=lang)


def build_weekly(
    comparison: dict,
    prev: Baseline,
    curr: Baseline,
    *,
    web_url: str,
    lang: Optional[str] = None,
):
    """Return (png_bytes, caption, button_rows)."""
    radar = comparison.get("radar", {})
    prev_vals = radar.get("previous", [])
    curr_vals = radar.get("current", [])

    labels, p_bars, c_bars, worse = [], [], [], []
    for idx, key in _AXES:
        if idx >= len(prev_vals) or idx >= len(curr_vals):
            continue
        labels.append(t(key, lang=lang))
        p_bars.append(prev_vals[idx])
        c_bars.append(curr_vals[idx])
        worse.append(curr_vals[idx] < prev_vals[idx])

    png = charts.weekly_bars(
        labels, p_bars, c_bars,
        prev_label=t("telegram.chart.last_week", lang=lang),
        curr_label=t("telegram.chart.this_week", lang=lang),
        worse_flags=worse,
    )

    caption = "\n\n".join([
        t("telegram.weekly.header", lang=lang),
        _subhead(comparison, lang=lang),
        _conclusion(prev, curr, lang=lang),
    ])

    buttons = [[Button(t("telegram.weekly.web_button", lang=lang), url=web_url)]]
    return png, caption, buttons
