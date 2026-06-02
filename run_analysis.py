"""
Jarvis - 실전 진입점 (Production Entry Point)
================================================================

네 로컬/서버에서 실제 바이낸스 키로 돌리는 진입점.
이 환경(네트워크 차단)에선 실행 불가 — 네 머신에서 돌려라.

실행 전 준비:
  1. pip install aiohttp cryptography
  2. 환경변수:
       export BINANCE_API_KEY="..."      # 읽기 전용 키
       export BINANCE_API_SECRET="..."
       export JARVIS_MASTER_KEY="..."     # 암호화용 (32바이트 base64)
         생성: python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
  3. python run_analysis.py

흐름:
  실제 바이낸스 API
    → 체결 가져오기 (fetch_user_fills)
    → 포지션 재조립 + 레버리지 해석
    → 거래 분석 + 개인 베이스라인
    → 시각화 JSON 출력 (프론트로 전달 가능)
"""
from __future__ import annotations
import asyncio
import os
import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal


async def main():
    # --- 0) 사전 점검 ---
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        print("❌ BINANCE_API_KEY / BINANCE_API_SECRET 환경변수가 필요합니다.")
        print("   읽기 전용 키만 사용하세요 (출금/거래 권한 불필요).")
        return

    from app.services.exchange import BinanceFuturesClient, ExchangeError
    from app.services.trade_history import (
        fetch_user_fills, reconstruct_positions, to_trades
    )
    from app.services.leverage import fetch_current_leverage
    from app.core.trade_analyzer import TradeAnalyzer
    from app.core.baseline import build_baseline, TradingStyle
    from app.services.viz_contract import (
        to_situation_breakdown, to_timeseries, to_journal_comparison
    )

    # 사용자 설정 (실제론 DB/온보딩에서)
    USER_STYLE = TradingStyle.SWING
    SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]  # 분석할 심볼 (실제론 거래한 것 자동 탐지)
    TESTNET = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

    client = BinanceFuturesClient(api_key, api_secret, testnet=TESTNET)

    try:
        # --- 1) 키 검증 ---
        print("🔑 API 키 검증 중...")
        valid = await client.validate_credentials()
        if not valid:
            print("❌ API 키가 유효하지 않거나 권한이 부족합니다.")
            return
        print("✅ 키 유효\n")

        # --- 2) 계좌 잔고 ---
        snapshot = await client.fetch_account_snapshot()
        equity = float(snapshot.equity)
        print(f"💰 현재 순자산: ${equity:,.2f}")
        print(f"📂 열린 포지션: {len(snapshot.positions)}개\n")

        # --- 3) 체결 기록 가져오기 (심볼별) ---
        print("📥 거래 기록 수집 중...")
        start_ms = int((datetime.now(timezone.utc) - timedelta(days=90)).timestamp() * 1000)
        all_fills = []
        for sym in SYMBOLS:
            try:
                fills = await fetch_user_fills(client, sym, start_ms)
                all_fills.extend(fills)
                print(f"   {sym}: {len(fills)}건")
            except ExchangeError as e:
                print(f"   {sym}: 조회 실패 ({e})")
        print()

        if not all_fills:
            print("거래 기록이 없습니다. 거래 후 다시 실행하세요.")
            return

        # --- 4) 포지션 재조립 + 레버리지 ---
        positions = reconstruct_positions(all_fills)
        lev_map = await fetch_current_leverage(client)
        trades = to_trades(positions, Decimal(str(equity)), leverage_lookup=lev_map)
        print(f"🔧 {len(all_fills)}체결 → {len(positions)}포지션 재조립\n")

        # --- 5) 분석 ---
        analyzer = TradeAnalyzer(trades)
        breakdown = to_situation_breakdown(analyzer)
        timeseries = to_timeseries(trades, start_equity=equity, weeks=12)

        # 구간 베이스라인 (최근 30일 vs 그 이전 30일)
        now = datetime.now(timezone.utc)
        recent = [t for t in trades if t.entry_time >= now - timedelta(days=30)]
        prior = [t for t in trades if now - timedelta(days=60) <= t.entry_time < now - timedelta(days=30)]

        comparison = None
        if recent and prior:
            curr_bl = build_baseline(recent, USER_STYLE, "최근 30일")
            prev_bl = build_baseline(prior, USER_STYLE, "이전 30일")
            comparison = to_journal_comparison(prev_bl, curr_bl, USER_STYLE)

        # --- 6) 결과 출력 ---
        o = breakdown["overall"]
        print("=" * 50)
        print(f"  분석 결과 (총 {o['n']}건)")
        print("=" * 50)
        print(f"  전체 승률 {o['win_rate']}% | 평균 {o['avg_pnl']}% | 누적 {o['total_pnl']}%\n")

        print("  [자동 인사이트]")
        for ins in breakdown["insights"]:
            print(f"    · {ins}")
        print()

        if comparison:
            print(f"  [구간 비교] {comparison['verdict']['text']}")
            if comparison["style_drift"]["drifted"]:
                print(f"    ⚠️ {comparison['style_drift']['message']}")
        print()

        # --- 7) 시각화 JSON 저장 (프론트로 전달용) ---
        payload = {
            "generated_at": now.isoformat(),
            "equity": equity,
            "breakdown": breakdown,
            "timeseries": timeseries,
            "comparison": comparison,
        }
        out_path = "jarvis_dashboard_data.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"💾 시각화 데이터 저장: {out_path}")
        print("   → 이 JSON을 프론트엔드 대시보드에 넣으면 화면이 렌더됩니다.")

    except ExchangeError as e:
        print(f"❌ 거래소 오류: {e}")
    finally:
        await client.close()


if __name__ == "__main__":
    print("""
    ╔════════════════════════════════════════╗
    ║   Jarvis - 거래 분석 (실전)            ║
    ║   생존이 전략이다.                      ║
    ╚════════════════════════════════════════╝
    """)
    asyncio.run(main())
