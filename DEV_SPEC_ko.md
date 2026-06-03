# Jarvis — 개발자 구현 명세서 (Developer Handoff Spec)

> 이 문서 하나로 개발자가 Jarvis 전체를 구현할 수 있도록 작성됨.
> 시스템 청사진이 "무엇이 어디 있나"라면, 이 문서는 "어떻게 만드나"다.
>
> **현재 상태**: 코어 분석 로직(17개 모듈, ~3,800줄)은 검증 완료. 남은 건 배관(DB·웹·인증·UI).

---

## 0. 한눈에 — 무엇이 끝났고 무엇을 만들어야 하나

| 계층 | 모듈 | 상태 |
|------|------|------|
| 데이터 소스 | `exchange.py` | ✅ 로직 완성 (실제 API 연결은 배포 환경) |
| 수집 | `trade_history.py`, `leverage.py`, `stoploss_tracker.py` | ✅ 완성 |
| 분석 | `trade_analyzer.py`, `journal.py`, `risk_math.py`, `jarvis_assess.py` | ✅ 완성 |
| 개인화 | `baseline.py` | ✅ 완성 |
| 검증 | `backtest_v2.py` | ✅ 완성 |
| 출력 | `viz_contract.py` | ✅ 완성 |
| 진입점 | `run_analysis.py` | ✅ 완성 (로컬 실행용) |
| **영속화** | DB 스키마 + 저장소 | ⏳ **구현 필요** |
| **웹 서버** | API 엔드포인트 | ⏳ **구현 필요** |
| **인증/빌링** | 가입·로그인·Stripe | ⏳ **구현 필요** |
| **워커** | 백그라운드 모니터링 루프 | ⏳ **구현 필요** (모듈은 있음) |
| **프론트엔드** | 대시보드 UI | ⏳ **구현 필요** (시각화 목업은 있음) |

---

## 1. 데이터 흐름 (핵심)

```
[거래소 API] ──┬─→ [체결 수집] → [포지션 재조립] → [레버리지 해석] ─┐
               │                                                      │
               └─→ [실시간 스냅샷] → [손절 관찰 누적] ─────────────┐  │
                                                                   ▼  ▼
                                                          [과거 분석 + 오답노트]
                                                          [실시간 리스크 평가]
                                                                   │
                                                                   ▼
                                                          [개인 베이스라인]
                                                          (평소의 나 vs 지금)
                                                                   │
                                                                   ▼
                                                          [시각화 JSON 계약]
                                                                   │
                                                  ┌────────────────┼────────────────┐
                                                  ▼                ▼                ▼
                                              [진단 화면]     [거울·저널]      [실시간 경고]
```

---

## 2. 모듈별 입출력 명세

### 2.1 수집 계층

**`exchange.py` — 거래소 추상화**
- 입력: API 키/시크릿 (읽기 전용)
- 출력: `AccountSnapshot`(잔고·포지션), 원시 체결 리스트
- 핵심: `ExchangeClient` ABC → `BinanceFuturesClient` 구현. 레이트리미터(토큰버킷), `ExchangeError(retryable, auth_failed)` 격리
- 구현 주의: 심볼별로 `userTrades` 호출해야 함 (바이낸스 제약)

**`trade_history.py` — 체결→포지션 재조립**
- 입력: `list[Fill]` (거래소 원시 체결)
- 출력: `list[ReconstructedPosition]` → `list[Trade]`
- 핵심 데이터 구조:
  ```
  Fill: symbol, time, side(BUY/SELL), price, qty, realized_pnl, commission
  ReconstructedPosition: symbol, side(LONG/SHORT), entry/exit_time,
                         entry_price(가중평균), qty(최대보유), realized_pnl
  Trade: symbol, side, entry/exit_time, pnl_pct, leverage,
         size_vs_avg, had_stop_loss
  ```
- 재조립 원리: 순포지션 0→0이 아님=진입, 0이 아님→0=종료. 그 사이 체결을 한 포지션으로. 불타기·부분청산 흡수.
- ⚠️ 한계: `had_stop_loss`는 API로 불명 → 기본 False, stoploss_tracker가 나중에 채움

**`leverage.py` — 레버리지 해석**
- 입력: `ReconstructedPosition`, (선택)증거금, (선택)현재설정맵
- 출력: `LeverageEstimate(value, source, confidence)`
- 우선순위: 증거금 역산(high) > 현재설정(low) > None(불가)
- 핵심: 신뢰도를 함께 반환. UI에서 "추정값" 표시 가능하게

**`stoploss_tracker.py` — 손절 관찰 (실시간↔과거 다리)**
- 입력: `StopLossObservation` (워커가 스냅샷마다)
- 출력: `StopLossVerdict(ever_had_sl, coverage_ratio, had_sl_when_risky)`
- 핵심: `had_sl_when_risky`(위험할 때 손절 있었나)가 곧 "심법 작동" 측정값
- `enrich_trades_with_sl()`로 과거 Trade의 had_stop_loss 빈칸 채움

### 2.2 분석 계층

**`risk_math.py` — 정밀 리스크 계산**
- 입력: `PositionInput`, equity
- 출력: `RiskMetrics` (stop_risk/liq_loss/liq_distance 등, 모두 Decimal)
- 핵심: 손절기준 risk와 청산기준 risk를 분리. 절대 틀린 숫자 안 냄

**`jarvis_assess.py` — 실시간 리스크 평가 (검증된 축만)**
- 입력: `RiskMetrics`, `AccountContext`, `BehaviorSignals`, atr_pct
- 출력: `JarvisAssessment(score, band, guardrail, coaching, headline)`
- 구조: 점수=변동성0.7+구조0.3 (검증된 축만) / 피해 가드레일(별도) / 행동 코칭(별도)
- 백테스트 근거: 생존·행동축은 점수 예측력 없어 분리함

**`trade_analyzer.py` — 상황별 승률**
- 입력: `list[Trade]`
- 출력: 세그먼트별 `SegmentStat` + 자동 인사이트
- 세그먼트: 연패/비중/재진입속도/시간대/보유기간/손절/레버리지/롱숏/종목

**`journal.py` — 오답노트 (기법 vs 심법)**
- 입력: `list[JournalEntry]` (사용자가 종료 시 태깅)
- 출력: 근거별/감정별 성적, `discipline_split`, `technique_vs_execution`
- 데이터 구조:
  ```
  JournalEntry: trade_id, symbol, closed_at, pnl_pct,
                rationale(BaseRationale|커스텀), emotion(EmotionalState),
                note(선택), followed_stop_loss(시스템이 채움)
  BaseRationale: 기술적/추세/지지저항/뉴스 + 충동/복수/FOMO
  EmotionalState: 평온/확신/조급/분노/과욕/지루함
  ```
- 핵심 출력: "좋은 근거로 들어갔지만 손절 안 지켜 진 거래 N건 → 심법 문제"
- ⚠️ 안전장치: MIN_RELIABLE_SAMPLE=8. 표본 적으면 "판단보류"

### 2.3 개인화 계층

**`baseline.py` — 평소의 나 vs 지금의 나**
- 입력: `list[Trade]`, `TradingStyle`(사용자 선언), 기간 라벨
- 출력: `Baseline`(구간 스냅샷), `PeriodComparison`, `StyleDrift`
- 3대 기능:
  - `compare_periods(prev, curr)`: 이번달 vs 지난달, 개선/악화 방향
  - `detect_style_drift`: 선언 스타일 vs 실제 (스윙인데 단타=충동 신호)
  - `evaluate_against_baseline`: "평소 8x인데 25x는 당신답지 않다"

### 2.4 출력 계층

**`viz_contract.py` — 시각화 데이터 계약**
- 입력: 분석 모듈 출력들
- 출력: 프론트가 그대로 먹는 JSON 3종
  - `to_journal_comparison`: 구간 비교 레이더 + 카드
  - `to_timeseries`: 주별 자본+승률 + 드로다운 + 꺾인지점
  - `to_situation_breakdown`: 상황별 승률 + 인사이트
- 핵심: 프론트는 데이터 출처(mock/실제)를 모름. 이 계약만 지키면 됨
- ⚠️ 알려진 이슈(실데이터 보정 필요): 거래 클러스터링 시 시계열 납작해짐 / 꺾인지점 과민(15%p 단순기준)

---

## 3. 구현해야 할 배관 (우선순위 순)

### Phase 1 — 영속화 + 실제 연결
**DB 스키마 (Postgres 권장)**
```
users           : id, email, hashed_pw, created_at, trading_style
api_credentials : user_id, ciphertext, salt, version  (crypto.py로 암호화)
trades          : user_id, symbol, side, entry/exit_time, pnl_pct,
                  leverage, size_vs_avg, had_stop_loss
journal_entries : user_id, trade_id, rationale, emotion, note, followed_sl
sl_observations : user_id, symbol, observed_at, has_sl, liq_distance
sl_verdicts     : user_id, symbol, entry_time, ever_had_sl,
                  coverage_ratio, had_sl_when_risky
baselines       : user_id, period_label, (스냅샷 필드들)
```
- 저장소 패턴: 현재 InMemory store들을 같은 인터페이스로 Postgres 구현
- `stoploss_tracker`의 `StopLossStore` Protocol이 이미 교체 가능하게 설계됨

**실제 API 연결**: `run_analysis.py`가 이미 진입점. 네트워크 되는 환경(서버)에서 환경변수만 세팅하면 작동

### Phase 2 — 인증 + 빌링
- 가입/로그인 (이메일+비번 또는 OAuth)
- API 키 입력 UI (읽기 전용 키만, 출금권한 거부 안내)
- Stripe 구독: Free(1거래소·기본) / Pro(다중·실시간·저널) / Elite(심층 행동 리포트)

### Phase 3 — 워커 + UI
**백그라운드 워커** (모듈은 있음, 루프로 묶기만)
```
매 N초:
  for each 활성 사용자:
    snapshot = exchange.fetch_account_snapshot()
    for each 포지션:
      assessment = jarvis_assess.assess(...)      # 실시간 평가
      if 위험: 알림 발송
      stoploss_tracker.observe(...)               # 손절 관찰 기록
    stoploss_tracker.reconcile(...)               # 닫힌 포지션 확정
```
**프론트엔드**: viz_contract JSON을 받아 렌더. 시각화 목업 5종 이미 있음 (복리시뮬/분석대시보드/구간레이더/시계열/진단)

### Phase 4 — 대화 인터페이스 (나중)
"자비스다운" 양방향. 지금은 보류.

### Phase 5 — 자동주문 (먼 미래)
법무·보험 갖춘 후 옵트인. v1은 읽기 전용 고수.

---

## 4. 사용자 여정 (구현 시 화면 순서)

```
1. 랜딩 → 자가진단 5문항 (데이터 불필요)
   → "기법형 부족 / 심법형 부족 / 도박단계 / 균형" 잠정 진단
2. "정확히 알려면 연동" → 가입 → 읽기전용 키 입력
3. 분석 실행 → 데이터 기반 진단 (첫 종합 화면)
4. 잔류 루프:
   - 거래 종료 시마다 오답노트 태깅 (클릭+선택적 한줄)
   - 주기적으로 거울(구간비교)·시계열 확인
   - 실시간 위험 시 경고·코칭
```

---

## 5. 절대 원칙 (구현 시 지킬 것)

1. **읽기 전용 키만**. 출금·거래 권한 거부. 자동주문 없음 (v1).
2. **틀린 숫자 안 냄**. 모든 금융 계산 Decimal. 측정 불가는 "계산 불가"로.
3. **측정 안 되는 건 점수에 안 섞음**. 행동·생존은 코칭·가드레일로 분리.
4. **표본 부족 시 판단 보류**. MIN_RELIABLE_SAMPLE 미만은 통계 안 냄.
5. **개인 기준으로 평가**. 절대 기준 아닌 "과거의 너" 대비.
6. **참모지 대리인 아님**. 정보·분석까지. 매수/매도 지시 안 함.
7. **키는 암호화 저장**. crypto.py 봉투암호화. 복호화는 워커 메모리에서만.

---

## 6. 검증 자산 (참고)

- 모든 모듈에 `__main__` 자가검증 포함 — `python3 모듈.py`로 즉시 확인
- `backtest_v2.py`: 알고리즘 예측력 검증 (합성 데이터)
- `demo_integration.py`: 전체 파이프라인 end-to-end 데모
- 백테스트 핵심 결과: "경고 따르면 청산 26%→13%, 최악손실 -55%→-32%"

---

*이 문서 + 17개 검증된 모듈 + 시각화 목업 = 개발 시작 준비 완료*
*철학: 생존이 전략이다. 기법 위에 심법을 쌓는다. 기회는 온다.*
