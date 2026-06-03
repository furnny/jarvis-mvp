# Jarvis — Developer Handoff Spec

> One document to let a developer (or Claude Code) implement all of Jarvis.
> The system blueprint is the "map"; this is the "construction drawing."
>
> **Current state**: Core analysis logic (17 modules, ~3,800 lines) is written and verified. What remains is plumbing (DB, web, auth, UI).

---

## 0. At a glance — what's done, what to build

| Layer | Modules | Status |
|-------|---------|--------|
| Data source | `exchange.py` | Done (real API connect = deploy env) |
| Ingestion | `trade_history.py`, `leverage.py`, `stoploss_tracker.py` | Done |
| Analysis | `trade_analyzer.py`, `journal.py`, `risk_math.py`, `jarvis_assess.py` | Done |
| Personalization | `baseline.py` | Done |
| Validation | `backtest_v2.py` | Done |
| Output | `viz_contract.py` | Done |
| Entry point | `run_analysis.py` | Done (local run) |
| **Persistence** | DB schema + stores | **TODO** |
| **Web server** | API endpoints | **TODO** |
| **Auth/billing** | signup, login, Stripe | **TODO** |
| **Worker** | background monitoring loop | **TODO** (modules exist) |
| **Frontend** | dashboard UI | **TODO** (mockups exist) |

---

## 1. Data flow

```
[Exchange API] --+--> [Fills] -> [Reconstruct positions] -> [Resolve leverage] --+
                 |                                                                |
                 +--> [Live snapshot] -> [Stop-loss observation] --------------+ |
                                                                               v v
                                                              [Past analysis + mistake notebook]
                                                              [Real-time risk assessment]
                                                                               |
                                                                               v
                                                              [Personal baseline]
                                                              (usual-you vs current-you)
                                                                               |
                                                                               v
                                                              [Viz JSON contract]
                                                                               |
                                              +--------------------------------+--------------------------------+
                                              v                                v                                v
                                       [Diagnostic]                    [Mirror / Journal]              [Real-time warnings]
```

---

## 2. Module I/O spec

### 2.1 Ingestion

**`exchange.py` — exchange abstraction**
- In: API key/secret (read-only)
- Out: `AccountSnapshot` (balance, positions), raw fills list
- Core: `ExchangeClient` ABC -> `BinanceFuturesClient`. Token-bucket rate limiter, `ExchangeError(retryable, auth_failed)` isolation.
- Note: must call `userTrades` per-symbol (Binance constraint).

**`trade_history.py` — fills -> positions**
- In: `list[Fill]` (raw exchange fills)
- Out: `list[ReconstructedPosition]` -> `list[Trade]`
- Data structures:
  ```
  Fill: symbol, time, side(BUY/SELL), price, qty, realized_pnl, commission
  ReconstructedPosition: symbol, side(LONG/SHORT), entry/exit_time,
                         entry_price(weighted avg), qty(max held), realized_pnl
  Trade: symbol, side, entry/exit_time, pnl_pct, leverage,
         size_vs_avg, had_stop_loss
  ```
- Reconstruction: net position 0->nonzero = open, nonzero->0 = close. Fills in between = one position. Absorbs add-ins (averaging) and partial closes.
- LIMITATION: `had_stop_loss` is unknowable from API -> defaults False; stoploss_tracker fills it later.

**`leverage.py` — leverage resolution**
- In: `ReconstructedPosition`, (optional) margin used, (optional) current-setting map
- Out: `LeverageEstimate(value, source, confidence)`
- Priority: margin inference (high) > current setting (low) > None (unknown)
- Core: returns confidence so UI can label "estimated."

**`stoploss_tracker.py` — stop-loss observation (realtime<->history bridge)**
- In: `StopLossObservation` (from worker, every snapshot)
- Out: `StopLossVerdict(ever_had_sl, coverage_ratio, had_sl_when_risky)`
- Core: `had_sl_when_risky` (was a stop present when it mattered) = the "discipline active" measure.
- `enrich_trades_with_sl()` backfills had_stop_loss on past Trades.

### 2.2 Analysis

**`risk_math.py` — precise risk math**
- In: `PositionInput`, equity
- Out: `RiskMetrics` (stop_risk / liq_loss / liq_distance, all Decimal)
- Core: separates stop-based risk from liquidation-based risk. Never emits a wrong number.

**`jarvis_assess.py` — real-time assessment (validated axes only)**
- In: `RiskMetrics`, `AccountContext`, `BehaviorSignals`, atr_pct
- Out: `JarvisAssessment(score, band, guardrail, coaching, headline)`
- Structure: score = volatility 0.7 + structure 0.3 (validated axes only) / damage guardrail (separate) / behavior coaching (separate)
- Rationale: backtest showed survival/behavior axes had no score-predictive power -> separated out.

**`trade_analyzer.py` — situational win rates**
- In: `list[Trade]`
- Out: per-segment `SegmentStat` + auto insights
- Segments: loss-streak / size / re-entry speed / hour / hold time / stop-loss / leverage / side / symbol

**`journal.py` — mistake notebook (technique vs discipline)**
- In: `list[JournalEntry]` (user tags on close)
- Out: per-rationale/per-emotion stats, `discipline_split`, `technique_vs_execution`
- Data structures:
  ```
  JournalEntry: trade_id, symbol, closed_at, pnl_pct,
                rationale(BaseRationale|custom), emotion(EmotionalState),
                note(optional), followed_stop_loss(system-filled)
  BaseRationale: technical/trend/support_resistance/news + impulse/revenge/fomo
  EmotionalState: calm/confident/anxious/revenge/greedy/bored
  ```
- Key output: "N trades entered on sound rationale but lost by not honoring the stop -> discipline problem."
- SAFEGUARD: MIN_RELIABLE_SAMPLE=8. Below it, "withhold judgment."

### 2.3 Personalization

**`baseline.py` — usual-you vs current-you**
- In: `list[Trade]`, `TradingStyle` (user-declared), period label
- Out: `Baseline` (period snapshot), `PeriodComparison`, `StyleDrift`
- Three functions:
  - `compare_periods(prev, curr)`: this month vs last, improving/worsening direction
  - `detect_style_drift`: declared style vs actual (swing but scalping = impulse signal)
  - `evaluate_against_baseline`: "you usually run 8x, 25x isn't like you"

### 2.4 Output

**`viz_contract.py` — visualization data contract**
- In: analysis module outputs
- Out: 3 JSON payloads the frontend renders directly
  - `to_journal_comparison`: period-comparison radar + cards
  - `to_timeseries`: weekly equity+winrate + drawdown + breakpoint
  - `to_situation_breakdown`: situational win rates + insights
- Core: frontend doesn't know the data source (mock/real). Just honor this contract.
- KNOWN ISSUES (fix with real data): timeseries flattens when trades cluster / breakpoint over-sensitive (simple 15%p rule).

---

## 3. Plumbing to build (priority order)

### Phase 1 — Persistence + real connection
**DB schema (Postgres recommended)**
```
users           : id, email, hashed_pw, created_at, trading_style
api_credentials : user_id, ciphertext, salt, version  (encrypt via crypto.py)
trades          : user_id, symbol, side, entry/exit_time, pnl_pct,
                  leverage, size_vs_avg, had_stop_loss
journal_entries : user_id, trade_id, rationale, emotion, note, followed_sl
sl_observations : user_id, symbol, observed_at, has_sl, liq_distance
sl_verdicts     : user_id, symbol, entry_time, ever_had_sl,
                  coverage_ratio, had_sl_when_risky
baselines       : user_id, period_label, (snapshot fields)
```
- Pattern: implement the existing InMemory stores against Postgres behind the same interface.
- `stoploss_tracker`'s `StopLossStore` Protocol is already swap-ready.

**Real API**: `run_analysis.py` is the entry point already. Needs a networked env + env vars.

### Phase 2 — Auth + billing
- Signup/login (email+pw or OAuth)
- API key input UI (read-only keys only; reject withdraw permission)
- Stripe subscription: Free (1 exchange, basic) / Pro (multi, real-time, journal) / Elite (deep behavioral reports)

### Phase 3 — Worker + UI
**Background worker** (modules exist; just wrap in a loop)
```
every N seconds:
  for each active user:
    snapshot = exchange.fetch_account_snapshot()
    for each position:
      assessment = jarvis_assess.assess(...)   # real-time
      if risky: send alert
      stoploss_tracker.observe(...)            # record SL observation
    stoploss_tracker.reconcile(...)            # finalize closed positions
```
**Frontend**: render viz_contract JSON. 5 visual mockups exist (compound sim / analysis dashboard / period radar / timeseries / diagnostic).

### Phase 4 — Conversational interface (later)
The "Jarvis-like" two-way chat. Deferred for now.

### Phase 5 — Auto-ordering (far future)
Opt-in after legal + insurance. v1 stays read-only.

---

## 4. User journey (screen order to implement)

```
1. Landing -> self-diagnostic, 5 questions (no data needed)
   -> tentative type: technique-deficient / discipline-deficient / gambling / balanced
2. "For the real diagnosis, connect" -> signup -> read-only key input
3. Run analysis -> data-driven diagnosis (first comprehensive screen)
4. Retention loop:
   - tag mistake notebook on each trade close (click + optional one-liner)
   - periodically check the mirror (period comparison) and timeseries
   - real-time warning/coaching on risk
```

---

## 5. Absolute principles (honor while implementing)

1. **Read-only keys only.** Reject withdraw/trade. No auto-ordering (v1).
2. **Never show a wrong number.** All financial math in Decimal. Unmeasurable -> "cannot compute."
3. **Don't fold unmeasurable axes into the score.** Behavior/survival stay as coaching/guardrail.
4. **Withhold judgment on small samples.** Below MIN_RELIABLE_SAMPLE, no stats.
5. **Evaluate against the user's own baseline,** not absolutes.
6. **Advisor, not agent.** Information and analysis only — no buy/sell instructions.
7. **Encrypt keys** (crypto.py envelope encryption); decrypt only in worker memory.

---

## 6. i18n

- User-facing strings: `locales/ko.json` + `locales/en.json` (key-for-key mirror).
- `from app.i18n import t, set_locale`; call `t("section.key", **kwargs)`.
- Add every new user-facing string to BOTH files. Never hardcode language in logic.
- Existing modules still have inline strings in their message-building f-strings — migrating those to `t()` is a wiring task (translations already exist in the locale files).

---

## 7. Validation assets

- Every module has a `__main__` self-test — `python3 <module>.py`.
- `backtest_v2.py`: algorithm predictive-power check (synthetic data).
- `demo_integration.py`: full end-to-end pipeline demo.
- Key backtest result: "following warnings cuts liquidation 26%->13%, worst-5% loss -55%->-32%."

---

*This doc + 17 verified modules + visual mockups = ready to start building.*
*Philosophy: survival is the strategy. Build discipline on top of technique. Opportunity will come.*
