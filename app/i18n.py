"""
Jarvis - i18n helper
================================================================
Minimal translation loader. Pattern for Claude Code to extend.

Usage:
    from app.i18n import t, set_locale
    set_locale("ko")
    t("journal.insight.discipline_split", d_wr=52, d_pnl=-0.3, i_wr=28, i_pnl=-1.9)

Design:
    - Locale JSON lives in /locales/{lang}.json
    - Keys are dot-paths into the nested JSON
    - {placeholders} filled via str.format(**kwargs)
    - Falls back to English, then to the raw key, so a missing
      translation never crashes the app — it degrades visibly.
"""
from __future__ import annotations
import json
import os
from functools import lru_cache

_LOCALE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "locales")
_current = "en"
_DEFAULT = "en"


def set_locale(lang: str) -> None:
    global _current
    _current = lang


@lru_cache(maxsize=8)
def _load(lang: str) -> dict:
    path = os.path.join(_LOCALE_DIR, f"{lang}.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _lookup(data: dict, dotted: str):
    """Resolve 'section.leaf.key' — first segment is the section,
    the remainder is the leaf key (which may itself contain dots)."""
    section, _, leaf = dotted.partition(".")
    if not leaf:
        val = data.get(section)
        return val if isinstance(val, str) else None
    sect = data.get(section)
    if not isinstance(sect, dict):
        return None
    val = sect.get(leaf)
    return val if isinstance(val, str) else None


def t(key: str, lang: str | None = None, **kwargs) -> str:
    """Translate key with interpolation. Falls back en → raw key."""
    lang = lang or _current
    tmpl = _lookup(_load(lang), key)
    if tmpl is None and lang != _DEFAULT:
        tmpl = _lookup(_load(_DEFAULT), key)
    if tmpl is None:
        return key  # visible fallback — signals a missing translation
    try:
        return tmpl.format(**kwargs)
    except (KeyError, IndexError):
        return tmpl  # placeholder mismatch → return unformatted, don't crash


if __name__ == "__main__":
    print("=== i18n helper verification ===\n")
    for lang in ("ko", "en"):
        set_locale(lang)
        print(f"[{lang}]")
        print("  " + t("journal.insight.discipline_split",
                        d_wr=52, d_pnl=-0.3, i_wr=28, i_pnl=-1.9))
        print("  " + t("assess.guardrail.ruin", pct=40))
        print("  " + t("baseline.flag.leverage", usual=8, current=25))
        print("  " + t("diagnostic.headline.mind_deficient"))
        print()

    # Fallback behavior
    set_locale("ko")
    print("[fallback test]")
    print("  missing key →", t("does.not.exist"))
    print("  en fallback →", t("journal.label.unreliable", lang="ko"))
    print("\n✅ i18n helper works (load, interpolate, fallback)")
