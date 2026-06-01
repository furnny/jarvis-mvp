# Jarvis — 프로덕션 재설계 문서 (v2)

> 실제 고객 돈이 걸린 SaaS로 출시하기 위한 정밀 재설계.
> 기존 MVP의 솔직한 평가 → 무엇이 왜 위험한가 → 어떻게 다시 짰는가.

---

## 1. 기존 MVP에 대한 솔직한 평가

### 잘한 점
- 제품 컨셉(advisory-only 리스크 모니터)이 명확하고 시장성이 있음
- 텔레그램 인터랙션 + discipline score라는 행동 유도 장치가 영리함
- 모듈 분리(config/models/engine/bot)의 기본 골격은 합리적

### 치명적 결함 (이대로 출시하면 사고)

| # | 문제 | 왜 치명적인가 | 결과 |
|---|------|--------------|------|
| 1 | **리스크 계산이 틀림** | `notional/balance`는 "포지션 크기"지 "리스크"가 아님. 10배로 1% 손절이면 실제 리스크는 전혀 다름 | 고객에게 거짓 숫자 제공 → 신뢰 즉시 붕괴 |
| 2 | **API 키 평문 저장** | DB 한 번 털리면 전 고객 키 유출 | 법적 책임 + 사업 종료급 사고 |
| 3 | **포지션 오픈 시간 미추적** | `created_time = utcnow()`로 매번 현재시각 → 손절 타임아웃 룰 작동 안 함 | 핵심 기능이 조용히 망가짐 |
| 4 | **15초 폴링의 한계** | 급락장에서 청산을 못 막음 | 가장 필요한 순간에 무용지물 |
| 5 | **멀티유저 동시성 부재** | 동기 호출 + 단일 루프, 한 유저 에러가 전체 중단 | 100명만 되어도 붕괴 |
| 6 | **상태가 in-memory** | 재시작하면 쿨다운/위반상태 소실, 멀티워커 공유 불가 | 스팸 알림 또는 알림 누락 |
| 7 | **빌링/인증/멀티테넌시 없음** | SaaS의 필수 요소가 통째로 빠짐 | 돈을 받을 수가 없음 |
| 8 | **테스트/관측성 없음** | 돈 걸린 시스템에 검증 장치 부재 | 버그를 고객이 먼저 발견 |

### 사업 관점 피드백
- **읽기 전용으로 시작한 결정은 옳다.** 거래 권한 키는 "고객 돈을 잃게 만들 수 있는 주체"가 되어 법적 노출이 폭증. 자동주문은 신뢰·보험·법무가 갖춰진 v2 상위 티어로.
- **"바이낸스 OAuth"는 존재하지 않는다.** 현실적 경로는 (a) 유저 직접 키 입력 또는 (b) Binance Broker 파트너십(기업 계약 필요). 초기엔 (a)뿐.
- **차별화 포인트가 약함.** 단순 알림은 TradingView 알림으로도 됨. Jarvis의 해자는 "행동 데이터 기반 규율 코칭"이어야 함 (discipline score를 진짜 분석으로).

---

## 2. 재설계 원칙

1. **정확성이 최우선** — 틀린 숫자보다 "계산 불가"가 낫다. 금융 계산은 Decimal.
2. **보안은 협상 불가** — 키는 봉투 암호화, 평문은 메모리에서도 최소 시간만.
3. **격리** — 한 유저의 실패가 다른 유저에게 전파되지 않는다.
4. **상태 외부화** — 워커는 무상태(stateless), 상태는 Redis/DB에.
5. **거래소 추상화** — 바이낸스 종속을 끊어 멀티 거래소 확장 가능.
6. **점진적 신뢰** — 읽기전용 → (신뢰 후) 자동주문. 처음부터 최소 권한.

---

## 3. 시스템 아키텍처 (v2)

```
                    ┌─────────────────────────────┐
                    │      Web / Telegram         │
                    │  (온보딩·결제·알림 수신)      │
                    └──────────────┬──────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
      ┌───────▼───────┐   ┌────────▼────────┐  ┌────────▼────────┐
      │   API (FastAPI)│   │  Telegram Bot   │  │ Stripe Webhook  │
      │  - 인증/온보딩  │   │  - 알림 전송     │  │ - 구독 상태     │
      │  - 키 등록      │   │  - 액션 버튼     │  │   동기화        │
      └───────┬───────┘   └────────┬────────┘  └────────┬────────┘
              │                    │                    │
              └────────────────────┼────────────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │     Postgres (영속 데이터)    │
                    │  users / credentials(암호화)  │
                    │  alerts / actions / subs      │
                    └──────────────┬──────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                                         │
   ┌──────────▼──────────┐                  ┌───────────▼──────────┐
   │  Monitor Workers     │                  │  Redis               │
   │  (수평 확장 가능)     │◄────상태 공유────►│  - 위반 상태(dedup)   │
   │  - 유저 샤딩          │                  │  - 포지션 first_seen  │
   │  - WebSocket 우선     │                  │  - 레이트리밋 토큰    │
   │  - 폴링 폴백          │                  │  - 작업 큐            │
   └──────────┬───────────┘                  └──────────────────────┘
              │
   ┌──────────▼──────────┐
   │  거래소 (읽기전용)    │
   │  Binance / (확장)    │
   └─────────────────────┘
```

### 모니터링 전략: WebSocket 우선 + 폴링 폴백
- **WebSocket(user data stream)**: 포지션·잔고 변경을 실시간 push로 받음 → 급락장 대응
- **폴링(폴백)**: WS 끊김/미지원 시 적응형 폴링(평상시 30s, 위험 포지션 보유 시 5s)
- 두 경로 모두 같은 `AccountSnapshot`으로 정규화 → 룰 엔진은 출처를 모름

---

## 4. 핵심 모듈 (실제 구현됨, 검증 완료)

### 4.1 `risk_math.py` — 정밀 리스크 계산
기존의 틀린 공식을 폐기하고 두 종류의 리스크를 분리:
- **Stop Risk** = `|진입가 − 손절가| × 수량` → 자본 대비 % (손절 있을 때만)
- **Liquidation Risk** = 청산 시 손실 추정 → 손절 없을 때의 안전판
- 모든 계산은 `Decimal` (float 누적오차 차단)

검증 결과: 10x·1손절 포지션을 기존 MVP는 "리스크 50%"로 오인했으나, 실제 손절 리스크는 10%로 정확히 산출.

### 4.2 `crypto.py` — 봉투 암호화
- HKDF로 salt별 DEK 파생 → Fernet(AES128-CBC + HMAC)로 암호화
- DB엔 (암호문 + salt + 버전)만 저장, 평문 부재
- 프로덕션은 `MasterKeyProvider`를 AWS KMS로 교체만 하면 됨 (인터페이스 동일)
- 키 로테이션 대비 `version` 필드

### 4.3 `rule_engine.py` — 상태 기반 룰 엔진
- **hysteresis**: 위반 시작 시 1회 알림, 지속 중엔 침묵, 해소 시 클리어 → 스팸 차단
- **포지션 age 추적**: `first_seen`을 외부 저장 → 손절 타임아웃 정확 동작
- `ViolationStore` 인터페이스 → 개발(메모리)/프로덕션(Redis) 교체
- 5개 룰: 청산임박 / 손절리스크초과 / 손절미설정 / 과레버리지 / 복수매매

### 4.4 `exchange.py` — 거래소 추상화
- `async` 비동기 + 토큰버킷 레이트리미터(IP 보호)
- `ExchangeError`로 인증실패/재시도가능 구분 → 한 유저 에러 격리
- account 단일 호출로 잔고+포지션 동시 취득(호출 최소화)
- 팩토리 패턴으로 거래소 추가 용이

---

## 5. 데이터 모델 (v2)

```sql
-- 유저 (인증은 이메일/매직링크 또는 텔레그램)
CREATE TABLE users (
    id              BIGSERIAL PRIMARY KEY,
    email           CITEXT UNIQUE,
    telegram_id     BIGINT UNIQUE,
    created_at      TIMESTAMPTZ DEFAULT now(),
    status          TEXT DEFAULT 'active'  -- active/suspended/deleted
);

-- 암호화된 거래소 자격증명 (평문 절대 없음)
CREATE TABLE exchange_credentials (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT REFERENCES users(id) ON DELETE CASCADE,
    exchange        TEXT NOT NULL,          -- 'binance'
    ciphertext      TEXT NOT NULL,          -- 봉투 암호화된 키+시크릿
    salt            TEXT NOT NULL,
    key_version     INT DEFAULT 1,
    permissions     TEXT[] DEFAULT '{read}',-- 읽기전용 강제
    is_valid        BOOLEAN DEFAULT true,   -- 검증 실패 시 false
    last_checked_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- 유저별 리스크 설정
CREATE TABLE risk_configs (
    user_id              BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    max_stop_risk_pct    NUMERIC DEFAULT 2.0,
    min_liq_distance_pct NUMERIC DEFAULT 8.0,
    max_eff_leverage     NUMERIC DEFAULT 10.0,
    no_sl_grace_minutes  NUMERIC DEFAULT 5.0,
    updated_at           TIMESTAMPTZ DEFAULT now()
);

-- 알림 로그 (감사·분석용)
CREATE TABLE alerts (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT REFERENCES users(id) ON DELETE CASCADE,
    rule_type       TEXT NOT NULL,
    severity        TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    body            TEXT,
    metrics         JSONB,                  -- 알림 시점 지표 스냅샷
    dedup_key       TEXT,
    delivered_at    TIMESTAMPTZ,
    acknowledged_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_alerts_user_time ON alerts(user_id, created_at DESC);

-- 유저 액션 (버튼 클릭) - discipline score 입력
CREATE TABLE user_actions (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT REFERENCES users(id) ON DELETE CASCADE,
    alert_id    BIGINT REFERENCES alerts(id) ON DELETE SET NULL,
    action_type TEXT NOT NULL,              -- ack/cooldown/will_reduce/will_set_sl
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- 구독 (Stripe 동기화)
CREATE TABLE subscriptions (
    user_id              BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    stripe_customer_id   TEXT UNIQUE,
    stripe_subscription_id TEXT UNIQUE,
    plan                 TEXT DEFAULT 'free',  -- free/pro/elite
    status               TEXT DEFAULT 'active',-- active/past_due/canceled
    current_period_end   TIMESTAMPTZ,
    updated_at           TIMESTAMPTZ DEFAULT now()
);
```

핵심: **자격증명은 별도 테이블 + 암호화**, **alerts/actions는 분석 자산** (discipline score와 코칭의 원천).

---

## 6. 모니터링 워커 (수평 확장)

```
worker_loop(shard_id, total_shards):
    while running:
        users = load_active_users_for_shard(shard_id, total_shards)
        # user_id % total_shards == shard_id 인 유저만 → 샤딩으로 수평 확장
        await asyncio.gather(*[
            guarded_check(user) for user in users
        ], return_exceptions=True)   # 한 유저 예외가 전체를 안 멈춤
        await adaptive_sleep()        # 위험 포지션 있으면 짧게, 없으면 길게

guarded_check(user):
    try:
        client = make_exchange_client(...)        # 복호화는 여기서만, 즉시 사용
        snapshot = await client.fetch_account_snapshot()
        equity = snapshot.equity
        seen_symbols = set()
        for pos in snapshot.positions:
            metrics = compute_risk(pos, equity)
            alerts = engine.evaluate(user.id, metrics, config, now)
            seen_symbols.add(pos.symbol)
            for alert in alerts:
                await dispatch(user, alert)        # 텔레그램 전송 + DB 기록
        # 닫힌 포지션 정리 (상태 클리어)
        reconcile_closed_positions(user.id, seen_symbols)
    except ExchangeError as e:
        if e.auth_failed:
            mark_credential_invalid(user.id)        # 유저에게 재등록 요청
        # retryable이면 다음 사이클에 자연 재시도
    finally:
        await client.close()
```

- **샤딩**: `total_shards`를 늘리면 워커를 수평 확장. 유저는 `user_id % shards`로 분배.
- **적응형 폴링**: 위험 포지션(청산 임박/손절 없음) 보유 유저는 5초, 그 외 30초.
- **WebSocket 통합**: WS 가능 유저는 push 이벤트로 즉시 평가, 폴링은 정합성 체크용.

---

## 7. 알림 전달 & 멱등성

- 모든 알림은 `dedup_key`로 멱등 처리 → 워커 재시도/중복 실행에도 한 번만 전송
- 텔레그램 전송 실패 시 지수 백오프 재시도, 영구 실패는 DB에 `delivered_at=NULL`로 남겨 추적
- 알림에 인라인 버튼: `확인` / `30분 쿨다운` / `손절 걸겠음` / `포지션 줄이겠음`
- 버튼 클릭 → `user_actions` 기록 → discipline score 갱신

---

## 8. Discipline Score (제대로)

기존의 단순 `100 - 위반*5`는 게임화 흉내일 뿐. v2는 **실제 행동 분석**:

```
주간 점수 = base
  − (미확인 위반 비율 × 가중)        # 경고를 무시하는가
  − (반복 위반 패턴 × 가중)          # 같은 실수 반복하는가
  + (약속 후 실제 이행률 × 가중)     # 손절 걸겠다 하고 실제 걸었는가 (다음 스냅샷에서 검증!)
  + (개선 추세 × 가중)               # 지난주 대비 나아지는가
```

핵심 차별점: **"손절 걸겠다"는 클릭 후 다음 스냅샷에서 실제로 손절이 생겼는지 검증**. 말과 행동의 일치를 측정 → 이게 진짜 코칭 데이터이자 Jarvis의 해자.

---

## 9. 빌링 (Stripe)

- **플랜**: Free(거래소 1개·기본 룰), Pro($19/mo, 다중 거래소·커스텀 룰·WebSocket 실시간), Elite($49/mo, 행동 분석 리포트·우선 알림)
- Stripe Checkout으로 결제, **webhook으로 구독 상태 동기화**(`customer.subscription.updated/deleted`)
- 워커는 `subscriptions.status`와 `plan`을 읽어 기능 게이팅 (예: Free는 폴링만, Pro는 WS)
- **유예**: `past_due`는 즉시 차단 말고 N일 유예 후 다운그레이드 (고객 이탈 방지)

---

## 10. 보안 & 컴플라이언스 체크리스트

- [x] API 키 봉투 암호화 (KMS 교체 가능)
- [x] 읽기 전용 권한 강제, 출금 권한 거부
- [ ] IP 화이트리스트 안내 (유저가 우리 워커 IP만 허용하도록)
- [ ] 키 복호화는 워커 메모리에서만, 로그에 절대 미기록
- [ ] 전송구간 TLS, DB 저장구간 암호화
- [ ] 감사 로그 (누가 언제 키 등록/삭제)
- [ ] GDPR/개인정보: 삭제 요청 시 cascade 삭제
- [ ] 면책 고지: "투자 조언 아님, 정보 제공 목적" (법무 검토 필수)
- [ ] Rate limit 준수로 거래소 BAN 방지

---

## 11. 관측성 (Observability)

- **구조화 로깅**(JSON): user_id, symbol, rule_type, latency
- **메트릭**: 워커 사이클 시간, 거래소 API 지연/에러율, 알림 전송 성공률, 활성 유저당 포지션 수
- **알람**(우리 자신을 위한): 워커 사이클이 임계 초과, 거래소 에러율 급증, WS 끊김
- **헬스체크**: `/health`가 DB·Redis·거래소 연결성까지 확인

---

## 12. 출시 로드맵

| 단계 | 범위 | 기간(목표) |
|------|------|-----------|
| **Phase 0** | 핵심 코어(완료): risk_math, crypto, rule_engine, exchange | ✅ |
| **Phase 1** | 폴링 워커 + 텔레그램 알림 + DB + 키 암호화 등록 | 2~3주 |
| **Phase 2** | Stripe 빌링 + 웹 온보딩 + 기능 게이팅 | 2주 |
| **Phase 3** | WebSocket 실시간 + 적응형 폴링 + 멀티거래소 | 3주 |
| **Phase 4** | Discipline 행동분석 + 주간 리포트 (해자 구축) | 3주 |
| **Phase 5** | (신뢰 후) 자동주문 옵트인 — 별도 법무·보험 | TBD |

---

## 13. 무엇을 검증했나

이 문서와 함께 제공되는 코드 중 다음은 **실제 실행·검증 완료**:
- `risk_math.py` — 두 케이스로 계산 정확성 확인 (손절기준/청산기준 분리)
- `crypto.py` — 암복호화 왕복 + salt 무작위성 확인
- `rule_engine.py` — hysteresis(중복억제) + 포지션 age 추적 동작 확인

나머지(워커 통합, 빌링, WS)는 인터페이스·의사코드로 설계를 고정했고, Phase 1~3에서 구현.

---

*문서 버전: 2.0 · 상태: 코어 검증 완료, 출시 준비 단계*
*면책: 본 시스템은 정보 제공 목적이며 투자 조언이 아님. 출시 전 법무 검토 필수.*
