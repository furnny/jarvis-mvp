"""
Daily summary (TELEGRAM_SPEC §1.3) — 3rd priority.

Metric chips + a mini equity sparkline (server-rendered) + a one-line
conclusion. The chart is SUPPORT; the sentence is the hero: "if you'd skipped
the impulse trades, today was green."

All data comes from viz_contract — `to_situation_breakdown()` for the overall
chips and `to_timeseries()` for the sparkline. The conclusion compares the
disciplined vs impulsive subset using the journal discipline split, which is
the same grounded comparison the web renders.
"""
from __future__ import annotations
from typing import Optional

from app.i18n import t
from app.telegram import charts


def _conclusion(split: dict, *, lang: Optional[str]) -> str:
    """split = MistakeNotebook.discipline_split() → {'disciplined': stat|None,
    'impulsive': stat|None} (stat carries n, total_pnl)."""
    disc = split.get("disciplined")
    imp = split.get("impulsive")
    if imp is None or imp.n == 0:
        if disc is not None and disc.n > 0:
            return t("telegram.daily.conclusion.clean", lang=lang)
        return t("telegram.daily.conclusion.insufficient", lang=lang)
    disc_pnl = f"{(disc.total_pnl if disc else 0.0):+.1f}%"
    return t(
        "telegram.daily.conclusion.impulse_drag", lang=lang,
        count=imp.n, impulse_pnl=f"{imp.total_pnl:+.1f}%", disciplined_pnl=disc_pnl,
    )


def build_daily(
    situation: dict,
    timeseries: dict,
    split: dict,
    *,
    date_label: str,
    lang: Optional[str] = None,
):
    """Return (png_bytes_or_None, caption). png is None on a no-trade day."""
    overall = situation.get("overall", {})
    n = overall.get("n", 0)

    if n == 0:
        caption = (f"{t('telegram.daily.header', lang=lang, date=date_label)}\n\n"
                   f"{t('telegram.daily.no_trades', lang=lang)}")
        return None, caption

    total_pnl = overall.get("total_pnl", 0)
    chips = "  ".join([
        f"[{t('telegram.daily.chip.trades', lang=lang, n=n)}]",
        f"[{t('telegram.daily.chip.winrate', lang=lang, wr=overall.get('win_rate', 0))}]",
        f"[{t('telegram.daily.chip.pnl', lang=lang, pnl=f'{total_pnl:+.1f}%')}]",
    ])

    caption = "\n\n".join([
        t("telegram.daily.header", lang=lang, date=date_label),
        chips,
        _conclusion(split, lang=lang),
    ])

    equity = timeseries.get("equity", [])
    png = charts.equity_sparkline(equity)
    return png, caption
