"""
Server-side chart rendering for Telegram (PNG bytes).

Per TELEGRAM_SPEC §3: charts are SUPPORT, not the hero — redesigned for a
phone, not shrunk from the web. Daily = mini equity sparkline; weekly = paired
horizontal bars (last week grey under this week colored).

All inputs come from viz_contract JSON — no separate analysis path. Rendering
uses the headless Agg backend so it works in a worker with no display.
"""
from __future__ import annotations
import io
from typing import Sequence

import matplotlib
matplotlib.use("Agg")  # headless: no display in the worker
import matplotlib.pyplot as plt  # noqa: E402

# Jarvis palette — calm, not alarmist
_INK = "#1c2330"
_GREY = "#b8c0cc"
_BLUE = "#2f6df0"
_GREEN = "#1f9d6b"
_RED = "#d65a4a"


def _to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def equity_sparkline(equity: Sequence[float], *, gained: bool | None = None) -> bytes:
    """Mini intraday/period capital curve. `equity` is viz_contract timeseries
    `equity` (the per-period closes). Color reflects net direction."""
    fig, ax = plt.subplots(figsize=(3.2, 1.0))
    if not equity:
        equity = [0]
    if gained is None:
        gained = len(equity) >= 2 and equity[-1] >= equity[0]
    color = _GREEN if gained else _RED
    x = list(range(len(equity)))
    ax.plot(x, equity, color=color, linewidth=2.0)
    ax.fill_between(x, equity, min(equity), color=color, alpha=0.12)
    # strip all chrome — it's a sparkline, the number lives in the caption
    ax.axis("off")
    return _to_png(fig)


def weekly_bars(
    labels: Sequence[str],
    prev: Sequence[float],
    curr: Sequence[float],
    *,
    prev_label: str,
    curr_label: str,
    worse_flags: Sequence[bool],
) -> bytes:
    """Paired horizontal bars: grey (last week) behind colored (this week).

    `worse_flags[i]` marks whether this-week is worse on that axis, so the
    colored bar turns red — translating the web radar's "shrinking area".
    Values are the normalized 0..100 axis scores from viz_contract radar.
    """
    n = len(labels)
    fig, ax = plt.subplots(figsize=(4.2, 0.55 * n + 0.8))
    y = list(range(n))[::-1]  # top-to-bottom in given order

    ax.barh(y, prev, height=0.55, color=_GREY, label=prev_label, zorder=1)
    colors = [_RED if w else _BLUE for w in worse_flags]
    ax.barh(y, curr, height=0.32, color=colors, label=curr_label, zorder=2)

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9, color=_INK)
    ax.set_xlim(0, 100)
    ax.set_xticks([])
    for spine in ("top", "right", "bottom"):
        ax.spines[spine].set_visible(False)
    ax.legend(loc="lower right", fontsize=7, frameon=False)
    return _to_png(fig)
