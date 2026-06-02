"""
Jarvis - 오답노트 (Trade Journal / Mistake Notebook)
================================================================

핵심 통찰 (사용자의 비밀):
  "완성된 트레이딩은 기법이 아니라 심법이다."
  기법이 부족해서 망하는 게 아니라, 검증된 기법을 안 지켜서 망한다.

오답노트의 두 가지 힘:
  1. 기술적: 진입 이유는 체결기록에 안 남음 → 사용자가 직접 태깅
  2. 심리적: '왜 들어갔지?'를 매번 적는 행위 자체가 다음 진입의 브레이크
     (적을 근거가 없는 거래는 안 하게 됨)

설계:
  근거(무엇을 보고)와 감정상태(어떤 마음으로)를 분리해 기록
  → "기술적 셋업인데 복수심 상태"처럼 교차 분석 가능
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from statistics import mean
from typing import Optional


# ----------------------------------------------------------------
# 진입 근거 (무엇을 보고 들어갔나) — 기본 + 커스텀
# ----------------------------------------------------------------
class BaseRationale(str, Enum):
    TECHNICAL = "기술적 셋업"
    TREND = "추세 추종"
    SUPPORT_RESISTANCE = "지지/저항"
    NEWS = "뉴스/이벤트"
    # 정직한 '나쁜 근거' — 죄책감 없이 누를 수 있어야 자각이 됨
    IMPULSE = "느낌/충동"
    REVENGE = "복수매매"
    FOMO = "FOMO"

    @property
    def is_disciplined(self) -> bool:
        """규율 있는 근거인가 (심법이 작동한 진입인가)"""
        return self in {BaseRationale.TECHNICAL, BaseRationale.TREND,
                        BaseRationale.SUPPORT_RESISTANCE, BaseRationale.NEWS}


# ----------------------------------------------------------------
# 진입 시 감정 상태 (어떤 마음으로) — 도파민/심법 측정
# ----------------------------------------------------------------
class EmotionalState(str, Enum):
    CALM = "평온"          # 시나리오대로, 침착
    CONFIDENT = "확신"      # 검증된 셋업에 대한 확신
    ANXIOUS = "조급"        # 빨리 들어가야 할 것 같은
    REVENGE = "분노/복수"   # 직전 손실 만회 욕구
    GREEDY = "과욕"         # 더 벌 수 있을 것 같은
    BORED = "지루함"        # 거래 안 하면 답답한 (도파민 갈망)

    @property
    def is_healthy(self) -> bool:
        return self in {EmotionalState.CALM, EmotionalState.CONFIDENT}


@dataclass
class JournalEntry:
    """한 거래의 오답노트 기록"""
    trade_id: str
    symbol: str
    closed_at: datetime
    pnl_pct: float
    # 사용자가 종료 시 남긴 것
    rationale: str                    # BaseRationale 값 또는 커스텀 문자열
    emotion: Optional[EmotionalState] # 진입 시 감정
    note: str = ""                    # 선택적 한 줄
    # 시스템이 아는 것 (안 물어봄)
    followed_stop_loss: Optional[bool] = None  # 손절 추적기가 판정

    @property
    def is_win(self) -> bool:
        return self.pnl_pct > 0

    @property
    def is_disciplined_rationale(self) -> bool:
        try:
            return BaseRationale(self.rationale).is_disciplined
        except ValueError:
            return True  # 커스텀 근거는 일단 규율 있는 것으로 (사용자가 정의한 기법)


@dataclass
class RationaleStat:
    label: str
    n: int
    win_rate: float
    avg_pnl: float
    total_pnl: float
    reliable: bool = True   # 표본이 충분한가 (작으면 통계가 거짓말)


# 신뢰할 만한 최소 표본. 이보다 적으면 '판단 보류'
MIN_RELIABLE_SAMPLE = 8


# ----------------------------------------------------------------
# 오답노트 분석
# ----------------------------------------------------------------
class MistakeNotebook:
    def __init__(self, entries: list[JournalEntry]):
        self.entries = entries

    def _stat(self, label, subset) -> Optional[RationaleStat]:
        if not subset:
            return None
        wins = sum(1 for e in subset if e.is_win)
        return RationaleStat(
            label=label, n=len(subset),
            win_rate=round(wins / len(subset) * 100, 1),
            avg_pnl=round(mean(e.pnl_pct for e in subset), 2),
            total_pnl=round(sum(e.pnl_pct for e in subset), 1),
            reliable=len(subset) >= MIN_RELIABLE_SAMPLE,
        )

    def by_rationale(self) -> list[RationaleStat]:
        """진입 근거별 성적 — 핵심 오답노트"""
        groups: dict[str, list] = {}
        for e in self.entries:
            groups.setdefault(e.rationale, []).append(e)
        stats = [s for s in (self._stat(k, v) for k, v in groups.items()) if s]
        # 규율 있는 근거 먼저, 그 안에서 승률순
        return sorted(stats, key=lambda s: -s.win_rate)

    def by_emotion(self) -> list[RationaleStat]:
        """감정 상태별 성적 — 심법 측정"""
        groups: dict[str, list] = {}
        for e in self.entries:
            if e.emotion:
                groups.setdefault(e.emotion.value, []).append(e)
        stats = [s for s in (self._stat(k, v) for k, v in groups.items()) if s]
        return sorted(stats, key=lambda s: -s.win_rate)

    def discipline_split(self) -> dict:
        """규율 있는 거래 vs 없는 거래 — 가장 중요한 비교"""
        disciplined = [e for e in self.entries if e.is_disciplined_rationale]
        impulsive = [e for e in self.entries if not e.is_disciplined_rationale]
        return {
            "disciplined": self._stat("규율 있는 진입", disciplined),
            "impulsive": self._stat("충동적 진입", impulsive),
        }

    def technique_vs_execution(self) -> Optional[str]:
        """
        기법 vs 심법의 간극을 짚음.
        '근거는 좋았는데 손절을 안 지켜서 진 경우'를 찾아냄.
        """
        # 규율 있는 근거 + 손절 안 지킴 + 패배
        broke_discipline = [
            e for e in self.entries
            if e.is_disciplined_rationale
            and e.followed_stop_loss is False
            and not e.is_win
        ]
        good_and_followed = [
            e for e in self.entries
            if e.is_disciplined_rationale and e.followed_stop_loss is True
        ]
        if broke_discipline and good_and_followed:
            wr_followed = sum(1 for e in good_and_followed if e.is_win) / len(good_and_followed) * 100
            return (f"좋은 근거로 들어갔지만 손절을 안 지켜 진 거래 {len(broke_discipline)}건. "
                    f"같은 근거로 손절을 지켰을 땐 승률 {wr_followed:.0f}%. "
                    f"→ 기법이 아니라 심법(손절 실행)이 문제입니다.")
        return None

    def insights(self) -> list[str]:
        out = []
        split = self.discipline_split()
        d, im = split["disciplined"], split["impulsive"]

        if d and im and im.n >= 3:
            out.append(
                f"규율 있는 진입 승률 {d.win_rate}% (평균 {d.avg_pnl:+.1f}%) vs "
                f"충동적 진입 승률 {im.win_rate}% (평균 {im.avg_pnl:+.1f}%). "
                f"충동 거래가 계좌를 갉아먹고 있습니다.")

        # 감정별 최악 (신뢰할 만한 표본만)
        emo_stats = [s for s in self.by_emotion() if s.reliable]
        if emo_stats:
            worst = min(emo_stats, key=lambda s: s.avg_pnl)
            if worst.avg_pnl < 0:
                out.append(f"'{worst.label}' 상태의 거래가 평균 {worst.avg_pnl}%로 가장 나쁩니다. "
                           f"이 감정일 땐 진입을 멈추는 게 유리합니다.")

        # 기법 vs 심법
        gap = self.technique_vs_execution()
        if gap:
            out.append(gap)

        if not out:
            out.append("아직 패턴을 찾기엔 기록이 적습니다. 복기를 계속 쌓아주세요.")
        return out

    def review_mistakes(self, limit=5) -> list[JournalEntry]:
        """오답노트의 핵심: 틀린 거래만 모아 다시 보기 (충동+패배 우선)"""
        mistakes = [e for e in self.entries
                    if not e.is_win and not e.is_disciplined_rationale]
        # trade_id로 중복 제거 (같은 거래 반복 표시 방지)
        seen = set()
        unique = []
        for e in sorted(mistakes, key=lambda e: e.pnl_pct):  # 큰 손실부터
            if e.trade_id not in seen:
                seen.add(e.trade_id)
                unique.append(e)
        return unique[:limit]


# ================================================================
# 검증
# ================================================================
if __name__ == "__main__":
    import random
    rnd = random.Random(3)
    base = datetime(2026, 1, 1)

    entries = []
    # 규율 있는 거래: 승률 높음
    for i in range(40):
        r = rnd.choice([BaseRationale.TECHNICAL, BaseRationale.SUPPORT_RESISTANCE, BaseRationale.TREND])
        win = rnd.random() < 0.62
        entries.append(JournalEntry(
            trade_id=f"t{i}", symbol="BTCUSDT", closed_at=base,
            pnl_pct=round(rnd.uniform(1,4) if win else -rnd.uniform(1,3), 2),
            rationale=r.value, emotion=rnd.choice([EmotionalState.CALM, EmotionalState.CONFIDENT]),
            followed_stop_loss=rnd.random() < 0.85,
        ))
    # 충동 거래: 승률 낮음
    for i in range(25):
        r = rnd.choice([BaseRationale.IMPULSE, BaseRationale.REVENGE, BaseRationale.FOMO])
        emo = {"느낌/충동": EmotionalState.ANXIOUS, "복수매매": EmotionalState.REVENGE,
               "FOMO": EmotionalState.GREEDY}[r.value]
        win = rnd.random() < 0.28
        entries.append(JournalEntry(
            trade_id=f"i{i}", symbol="BTCUSDT", closed_at=base,
            pnl_pct=round(rnd.uniform(1,3) if win else -rnd.uniform(2,6), 2),
            rationale=r.value, emotion=emo,
            followed_stop_loss=rnd.random() < 0.3,
            note="그냥 오를 것 같았음" if r==BaseRationale.IMPULSE else "",
        ))    # 좋은 근거인데 손절 안 지켜 진 케이스 (기법vs심법)
    for i in range(8):
        entries.append(JournalEntry(
            trade_id=f"b{i}", symbol="ETHUSDT", closed_at=base,
            pnl_pct=round(-rnd.uniform(4,9),2),
            rationale=BaseRationale.TECHNICAL.value, emotion=EmotionalState.ANXIOUS,
            followed_stop_loss=False, note="손절 자리 왔는데 버팀",
        ))

    nb = MistakeNotebook(entries)

    print("=== 오답노트 검증 ===\n")
    print("[진입 근거별 성적]")
    for s in nb.by_rationale():
        disc = "" if BaseRationale(s.label).is_disciplined else "  ⚠️충동"
        rel = "" if s.reliable else "  (표본부족·판단보류)"
        print(f"  {s.label:12s} n={s.n:3d}  승률 {s.win_rate:5.1f}%  평균 {s.avg_pnl:+6.2f}%{disc}{rel}")

    print("\n[감정 상태별 성적]")
    for s in nb.by_emotion():
        print(f"  {s.label:10s} n={s.n:3d}  승률 {s.win_rate:5.1f}%  평균 {s.avg_pnl:+6.2f}%")

    print("\n[규율 vs 충동]")
    split = nb.discipline_split()
    for k in ["disciplined", "impulsive"]:
        s = split[k]
        if s:
            print(f"  {s.label:12s} n={s.n:3d}  승률 {s.win_rate:5.1f}%  평균 {s.avg_pnl:+6.2f}%")

    print("\n[자동 인사이트]")
    for ins in nb.insights():
        print(f"  · {ins}")

    print("\n[오답노트 — 다시 볼 거래]")
    for e in nb.review_mistakes(3):
        note = f' "{e.note}"' if e.note else ""
        print(f"  {e.rationale} {e.pnl_pct:+.1f}%{note}")

    print("\n✅ 오답노트 검증 완료")
