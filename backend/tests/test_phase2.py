"""Phase 2 tests: starter universe, new strategies, backtest engine, and bot fleet API."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.backtest.engine import (
    run_backtest,
    run_portfolio_backtest,
    run_rotation_backtest,
    run_rotation_walk_forward,
    run_walk_forward,
)
from app.config import STARTER_UNIVERSE
from app.main import create_app
from app.models import (
    BacktestRequest,
    BotCreateRequest,
    PortfolioBacktestRequest,
    PortfolioBotConfig,
    PriceBar,
    RotationBacktestRequest,
    RotationWalkForwardRequest,
    WalkForwardRequest,
)
from app.storage.db import TradingStore
from app.strategies.dual_momentum import compute_dual_momentum_targets
from app.strategies.rsi2_pullback_swing import Rsi2PullbackSwingStrategy, rsi2_swing_signal
from app.strategies.vwap_rsi_reversion import VwapRsiMeanReversionStrategy, vwap_rsi_signal


def _make_bar(time: str, close: float, low: float | None = None, high: float | None = None) -> PriceBar:
    return PriceBar(
        time=time,
        open=close,
        high=high if high is not None else close + 0.5,
        low=low if low is not None else close - 0.5,
        close=close,
        volume=50_000,
    )


def test_starter_universe_excludes_google_and_includes_nvda() -> None:
    assert "NVDA" in STARTER_UNIVERSE
    assert "GOOGL" not in STARTER_UNIVERSE
    assert "GOOG" not in STARTER_UNIVERSE
    assert "SPY" in STARTER_UNIVERSE


def test_vwap_rsi_mean_reversion_signals() -> None:
    # Flat around 100 then sharp dip to 94 -> oversold below VWAP
    bars = [_make_bar(f"2026-11-11 10:{i:02d}", 100.0) for i in range(18)]
    bars.append(_make_bar("2026-11-11 10:18", 97.0))
    bars.append(_make_bar("2026-11-11 10:19", 94.0))

    action, _, target = vwap_rsi_signal(
        bars,
        has_position=False,
        params={"rsi_period": 14, "rsi_oversold": 35, "vwap_dev_pct": 0.4},
    )
    assert action == "BUY"
    assert target is not None and target > 94.0

    # Bounce back above VWAP -> EXIT
    bars.append(_make_bar("2026-11-11 10:20", 101.0))
    action_exit, _, _ = vwap_rsi_signal(bars, has_position=True, params={"rsi_period": 14})
    assert action_exit == "EXIT"


def test_rsi2_pullback_swing_signals() -> None:
    # Steady uptrend for 210 days, then 2-day pullback while still > SMA200
    bars = [_make_bar(f"2026-01-{i+1:02d}", 100.0 + i * 0.25) for i in range(210)]
    peak = bars[-1].close
    bars.append(_make_bar("2026-08-01", peak - 4.0))
    bars.append(_make_bar("2026-08-02", peak - 8.0))

    action, _ = rsi2_swing_signal(
        bars,
        has_position=False,
        params={"rsi_period": 2, "rsi_buy": 15, "trend_sma": 200, "exit_sma": 5},
    )
    assert action == "BUY"

    # Strong rebound above 5-SMA -> EXIT
    bars.append(_make_bar("2026-08-03", peak + 2.0))
    action_exit, _ = rsi2_swing_signal(
        bars,
        has_position=True,
        params={"rsi_period": 2, "rsi_buy": 15, "trend_sma": 200, "exit_sma": 5},
    )
    assert action_exit == "EXIT"


def test_backtest_engine_triggers_stop_loss_bracket() -> None:
    bars = [_make_bar(f"2026-11-11 09:{i:02d}", 100.0) for i in range(20)]
    bars.append(_make_bar("2026-11-11 09:20", 97.0))
    bars.append(_make_bar("2026-11-11 09:21", 94.0))  # triggers BUY around 94.0
    bars.append(_make_bar("2026-11-11 09:22", 91.5, low=91.0))  # hits 2% stop-loss (~92.12)
    bars.append(_make_bar("2026-11-11 09:23", 95.0))

    req = BacktestRequest(
        strategy_type="vwap_rsi_reversion",
        symbol="SPY",
        timeframe="5 mins",
        initial_capital=10_000.0,
        quantity=10,
        stop_loss_pct=2.0,
        take_profit_pct=5.0,
        params={"rsi_period": 14, "rsi_oversold": 35, "vwap_dev_pct": 0.4},
    )
    res = run_backtest(bars, req)
    assert res.total_trades >= 1
    assert res.trades[0].exit_reason == "Stop-Loss"
    assert res.max_drawdown_pct >= 0.0
    assert res.benchmark is not None
    assert res.benchmark.label == "Buy & Hold (SPY)"
    assert res.exposure_pct > 0.0
    assert res.equity_curve[0].benchmark_equity is not None


def test_bot_fleet_and_backtest_endpoints(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "phase2.db"
    monkeypatch.setenv("DB_PATH", str(db_path))

    # Pre-seed SQLite historical cache so backtest runs offline deterministically
    store = TradingStore(db_path)
    bars = [_make_bar(f"2026-11-11 10:{i:02d}", 100.0) for i in range(20)]
    bars.append(_make_bar("2026-11-11 10:20", 96.0))
    bars.append(_make_bar("2026-11-11 10:21", 93.0))
    bars.append(_make_bar("2026-11-11 10:22", 101.0))
    store.upsert_historical_bars("NVDA", "5 mins", bars, "yfinance")

    from app import config

    config.get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as client:
            uni = client.get("/api/universe").json()
            assert "NVDA" in uni["symbols"]
            assert "GOOGL" not in uni["symbols"]
            assert len(uni["cache"]) == 1

            # Create custom bot
            created = client.post(
                "/api/bots",
                json={
                    "bot_id": "nvda-meanrev-1",
                    "strategy_type": "vwap_rsi_reversion",
                    "symbol": "NVDA",
                    "timeframe": "5 mins",
                    "stop_loss_pct": 1.5,
                    "take_profit_pct": 2.0,
                    "params": {"quantity": 5},
                },
            )
            assert created.status_code == 200
            assert created.json()["name"] == "nvda-meanrev-1"

            started = client.post("/api/bots/nvda-meanrev-1/start")
            assert started.status_code == 200
            assert started.json()["running"] is True

            stopped = client.post("/api/bots/nvda-meanrev-1/stop")
            assert stopped.status_code == 200
            assert stopped.json()["running"] is False

            deleted = client.delete("/api/bots/nvda-meanrev-1")
            assert deleted.status_code == 200

            bt = client.post(
                "/api/backtest/run",
                json={
                    "strategy_type": "vwap_rsi_reversion",
                    "symbol": "NVDA",
                    "timeframe": "5 mins",
                    "initial_capital": 25000,
                    "quantity": 10,
                    "stop_loss_pct": 2.0,
                    "params": {"rsi_period": 14, "rsi_oversold": 40, "vwap_dev_pct": 0.4},
                },
            )
            assert bt.status_code == 200
            assert bt.json()["bars_tested"] == len(bars)
            assert bt.json()["total_trades"] >= 1
    finally:
        config.get_settings.cache_clear()


async def test_strategy_auto_selects_default_timeframe_and_passes_to_broker() -> None:
    bot_swing = BotCreateRequest(
        bot_id="auto-swing",
        strategy_type="rsi2_pullback_swing",
        symbol="QQQ",
    )
    assert bot_swing.timeframe == "1 day"

    bot_intraday = BotCreateRequest(
        bot_id="auto-vwap",
        strategy_type="vwap_rsi_reversion",
        symbol="SPY",
    )
    assert bot_intraday.timeframe == "5 mins"

    bt_shock = BacktestRequest(strategy_type="shock_reversion", symbol="NVDA")
    assert bt_shock.timeframe == "5 mins"

    bt_swing = BacktestRequest(strategy_type="rsi2_pullback_swing", symbol="QQQ")
    assert bt_swing.timeframe == "1 day"

    # Explicit override is still preserved
    bot_override = BotCreateRequest(
        bot_id="custom-15m",
        strategy_type="vwap_rsi_reversion",
        symbol="SPY",
        timeframe="15 mins",
    )
    assert bot_override.timeframe == "15 mins"

    # Live strategy evaluation passes self.timeframe into broker.get_historical_bars
    captured: list[tuple[str, int, str]] = []

    class RecordingBroker:
        async def get_historical_bars(self, symbol: str, count: int = 60, timeframe: str = "5 mins"):
            captured.append((symbol, count, timeframe))
            return []

        async def get_positions(self):
            return []

    swing_strat = Rsi2PullbackSwingStrategy(bot_id="swing", symbol="QQQ")
    vwap_strat = VwapRsiMeanReversionStrategy(bot_id="vwap", symbol="SPY")

    await swing_strat.evaluate(RecordingBroker(), lambda o: o, lambda *a: None)  # type: ignore[arg-type]
    await vwap_strat.evaluate(RecordingBroker(), lambda o: o, lambda *a: None)  # type: ignore[arg-type]

    assert captured == [("QQQ", 210, "1 day"), ("SPY", 60, "5 mins")]


def test_portfolio_backtest_shared_cash_unique_symbols_and_api(tmp_path: Path, monkeypatch) -> None:
    import pytest
    from pydantic import ValidationError

    # Reject duplicate symbols (1 bot per symbol)
    with pytest.raises(ValidationError):
        PortfolioBacktestRequest(
            bots=[
                PortfolioBotConfig(strategy_type="vwap_rsi_reversion", symbol="SPY"),
                PortfolioBotConfig(strategy_type="shock_reversion", symbol="SPY"),
            ]
        )

    # Two bots (SPY & QQQ) triggering dips at the same bar
    spy_bars = [_make_bar(f"2026-11-11 10:{i:02d}", 100.0) for i in range(20)]
    spy_bars.append(_make_bar("2026-11-11 10:20", 96.0))
    spy_bars.append(_make_bar("2026-11-11 10:21", 93.0))
    spy_bars.append(_make_bar("2026-11-11 10:22", 101.0))

    qqq_bars = [_make_bar(f"2026-11-11 10:{i:02d}", 100.0) for i in range(20)]
    qqq_bars.append(_make_bar("2026-11-11 10:20", 96.5))
    qqq_bars.append(_make_bar("2026-11-11 10:21", 93.5))
    qqq_bars.append(_make_bar("2026-11-11 10:22", 101.5))

    # Tight cash ($1,200): can afford 10 shares of SPY (~$930) but not QQQ simultaneously -> cash skip!
    tight_req = PortfolioBacktestRequest(
        bots=[
            PortfolioBotConfig(
                bot_id="spy-bot",
                strategy_type="vwap_rsi_reversion",
                symbol="SPY",
                quantity=10,
                params={"rsi_period": 14, "rsi_oversold": 40, "vwap_dev_pct": 0.4},
            ),
            PortfolioBotConfig(
                bot_id="qqq-bot",
                strategy_type="vwap_rsi_reversion",
                symbol="QQQ",
                quantity=10,
                params={"rsi_period": 14, "rsi_oversold": 40, "vwap_dev_pct": 0.4},
            ),
        ],
        initial_capital=1_200.0,
    )
    tight_res = run_portfolio_backtest({"spy-bot": spy_bars, "qqq-bot": qqq_bars}, tight_req)
    assert tight_res.max_concurrent_positions == 1
    assert tight_res.skipped_for_cash >= 1
    assert tight_res.benchmark is not None
    assert tight_res.benchmark.label == "Equal-Weight Buy & Hold (2 symbols)"

    # Plenty of cash ($20,000): both bots run concurrently via API endpoint
    db_path = tmp_path / "portfolio.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    store = TradingStore(db_path)
    store.upsert_historical_bars("SPY", "5 mins", spy_bars, "yfinance")
    store.upsert_historical_bars("QQQ", "5 mins", qqq_bars, "yfinance")

    from app import config

    config.get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as client:
            resp = client.post(
                "/api/backtest/portfolio",
                json={
                    "bots": [
                        {
                            "bot_id": "spy-bot",
                            "strategy_type": "vwap_rsi_reversion",
                            "symbol": "SPY",
                            "quantity": 10,
                            "params": {"rsi_period": 14, "rsi_oversold": 40, "vwap_dev_pct": 0.4},
                        },
                        {
                            "bot_id": "qqq-bot",
                            "strategy_type": "vwap_rsi_reversion",
                            "symbol": "QQQ",
                            "quantity": 10,
                            "params": {"rsi_period": 14, "rsi_oversold": 40, "vwap_dev_pct": 0.4},
                        },
                    ],
                    "initial_capital": 20000,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["max_concurrent_positions"] == 2
            assert data["skipped_for_cash"] == 0
            assert len(data["bot_results"]) == 2
            assert data["total_trades"] >= 2
    finally:
        config.get_settings.cache_clear()


def test_compounded_pct_equity_sizing_scales_position_size() -> None:
    # Build two consecutive oversold dips separated by a big gain so equity grows
    bars = [_make_bar(f"2026-11-11 10:{i:02d}", 100.0) for i in range(18)]
    bars.append(_make_bar("2026-11-11 10:18", 95.0))
    bars.append(_make_bar("2026-11-11 10:19", 90.0))  # 1st entry at ~90
    bars.append(_make_bar("2026-11-11 10:20", 120.0))  # exit at ~120 -> equity grows ~33%
    for j in range(10):
        bars.append(_make_bar(f"2026-11-11 10:{21+j:02d}", 120.0))
    bars.append(_make_bar("2026-11-11 10:31", 95.0))  # 2nd entry at same 95 -> buys MORE shares
    bars.append(_make_bar("2026-11-11 10:32", 110.0))

    res = run_backtest(
        bars,
        BacktestRequest(
            strategy_type="vwap_rsi_reversion",
            symbol="SPY",
            timeframe="5 mins",
            initial_capital=10_000.0,
            quantity=10,
            allocation_pct=100.0,
            stop_loss_pct=None,
            take_profit_pct=None,
            params={"rsi_period": 14, "rsi_oversold": 45, "vwap_dev_pct": 0.4},
        ),
    )
    assert res.total_trades >= 2
    assert res.trades[0].quantity > 100  # ~10000 / 90 = 111 shares
    assert res.trades[1].quantity > res.trades[0].quantity

    # Portfolio % equity sizing respects shared cash across 2 bots
    port_res = run_portfolio_backtest(
        {"b1": bars, "b2": bars},
        PortfolioBacktestRequest(
            bots=[
                PortfolioBotConfig(
                    bot_id="b1",
                    strategy_type="vwap_rsi_reversion",
                    symbol="SPY",
                    allocation_pct=50.0,
                    stop_loss_pct=None,
                    params={"rsi_period": 14, "rsi_oversold": 45, "vwap_dev_pct": 0.4},
                ),
                PortfolioBotConfig(
                    bot_id="b2",
                    strategy_type="vwap_rsi_reversion",
                    symbol="QQQ",
                    allocation_pct=50.0,
                    stop_loss_pct=None,
                    params={"rsi_period": 14, "rsi_oversold": 45, "vwap_dev_pct": 0.4},
                ),
            ],
            initial_capital=10_000.0,
        ),
    )
    assert port_res.max_concurrent_positions == 2
    assert port_res.total_trades >= 4


def test_walk_forward_split_auto_tune_and_api(tmp_path: Path, monkeypatch) -> None:
    # Build 80 bars with an oscillating trend-up-then-down-then-up pattern across both halves
    bars: list[PriceBar] = []
    for i in range(80):
        cycle = i % 20
        price = 100.0 + (cycle * 1.0 if cycle < 10 else (20 - cycle) * 1.0)
        bars.append(_make_bar(f"2026-10-{(i // 24) + 1:02d} {i % 24:02d}:00", price))

    wf_req = WalkForwardRequest(
        strategy_type="sma_crossover",
        symbol="SPY",
        timeframe="5 mins",
        initial_capital=25_000.0,
        allocation_pct=100.0,
        stop_loss_pct=None,
        split_ratio=0.6,
        auto_tune=True,
    )
    wf_res = run_walk_forward(bars, wf_req)
    assert wf_res.candidates_tested >= 4
    assert wf_res.in_sample.bars_tested == 48
    assert wf_res.out_of_sample.bars_tested == 32
    assert wf_res.split_time == bars[48].time
    assert "fast_period" in wf_res.selected_params
    assert wf_res.verdict in ("ROBUST", "DEGRADED", "OVERFIT_WARNING", "NO_TRADES")

    # Verify API endpoint
    db_path = tmp_path / "wf.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    store = TradingStore(db_path)
    store.upsert_historical_bars("SPY", "5 mins", bars, "yfinance")

    from app import config

    config.get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as client:
            resp = client.post(
                "/api/backtest/walk-forward",
                json={
                    "strategy_type": "sma_crossover",
                    "symbol": "SPY",
                    "timeframe": "5 mins",
                    "initial_capital": 25000,
                    "allocation_pct": 100,
                    "split_ratio": 0.6,
                    "auto_tune": True,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["candidates_tested"] >= 4
            assert data["in_sample"]["bars_tested"] == 48
            assert data["out_of_sample"]["bars_tested"] == 32
    finally:
        config.get_settings.cache_clear()


def test_dual_momentum_rotation_and_walk_forward() -> None:
    from datetime import date, timedelta

    start = date(2025, 1, 1)
    dates = [(start + timedelta(days=i)).isoformat() for i in range(420)]

    # SPY & NVDA trend up strongly; IWM trends down; TLT trends up modestly
    bars_by_sym = {
        "SPY": [_make_bar(d, 100.0 + i * 0.25) for i, d in enumerate(dates)],
        "NVDA": [_make_bar(d, 100.0 + i * 0.50) for i, d in enumerate(dates)],
        "IWM": [_make_bar(d, 200.0 - i * 0.30) for i, d in enumerate(dates)],
        "TLT": [_make_bar(d, 90.0 + i * 0.08) for i, d in enumerate(dates)],
    }

    idx_map = {sym: len(bars) - 1 for sym, bars in bars_by_sym.items()}
    targets, _ = compute_dual_momentum_targets(
        bars_by_sym, idx_map, top_n=3, lookback_days=126, trend_sma=200, defensive_symbol="TLT"
    )
    # Top 2 offensive uptrends (NVDA, SPY) + 1 unfilled slot rotated to defensive TLT (IWM excluded!)
    assert "NVDA" in targets and "SPY" in targets and "TLT" in targets
    assert "IWM" not in targets

    rot_res = run_rotation_backtest(
        bars_by_sym,
        RotationBacktestRequest(
            symbols=["SPY", "NVDA", "IWM", "TLT"],
            defensive_symbol="TLT",
            top_n=2,
            lookback_days=63,
            trend_sma=100,
            initial_capital=25_000.0,
        ),
    )
    assert rot_res.total_return_pct > 0
    assert rot_res.benchmark is not None
    assert rot_res.benchmark.label == "Buy & Hold (SPY)"

    wf_res = run_rotation_walk_forward(
        bars_by_sym,
        RotationWalkForwardRequest(
            symbols=["SPY", "NVDA", "IWM", "TLT"],
            defensive_symbol="TLT",
            top_n=2,
            lookback_days=63,
            trend_sma=100,
            split_ratio=0.55,
            auto_tune=False,
        ),
    )
    assert wf_res.in_sample.total_return_pct > 0
    assert wf_res.out_of_sample.total_return_pct > 0

    # Windowed walk-forward (start_date/end_date) uses pre-start_date bars for 200-SMA warmup
    mid_start = dates[150]
    mid_end = dates[-1]
    window_wf = run_rotation_walk_forward(
        bars_by_sym,
        RotationWalkForwardRequest(
            symbols=["SPY", "NVDA", "IWM", "TLT"],
            defensive_symbol="TLT",
            top_n=2,
            lookback_days=63,
            trend_sma=120,
            start_date=mid_start,
            end_date=mid_end,
            split_ratio=0.5,
            auto_tune=False,
        ),
    )
    assert window_wf.in_sample.equity_curve[0].time[:10] >= mid_start
    assert window_wf.in_sample.total_return_pct > 0
    assert window_wf.out_of_sample.total_return_pct > 0


def test_bot_budget_resolution_and_dual_momentum_cap() -> None:
    import asyncio
    from datetime import date, timedelta
    from app.broker.paper_simulator import PaperSimulator
    from app.strategies.dual_momentum import DualMomentumRotationStrategy
    from app.strategies.sma_crossover import SmaCrossoverStrategy

    async def _run() -> None:
        broker = PaperSimulator(initial_cash=10_000.0)
        await broker.connect()

        bot = SmaCrossoverStrategy(
            bot_id="test-sma",
            symbol="SPY",
            params={"quantity": 10},
        )
        assert await bot.resolve_entry_quantity(broker, 100.0, 10) == 10

        bot.params["max_allocation_pct"] = 25.0
        assert await bot.resolve_entry_quantity(broker, 100.0, 10) == 25

        bot.params["max_allocation_pct"] = 50.0
        bot.params["max_capital"] = 1_500.0
        assert await bot.resolve_entry_quantity(broker, 100.0, 10) == 15

        # DualMomentumRotationStrategy caps capital_pool by max_capital
        start = date(2025, 1, 1)
        dates = [(start + timedelta(days=i)).isoformat() for i in range(250)]
        bars_by_sym = {
            "SPY": [_make_bar(d, 100.0 + i * 0.2) for i, d in enumerate(dates)],
            "TLT": [_make_bar(d, 90.0 + i * 0.05) for i, d in enumerate(dates)],
        }

        async def _fake_hist(symbol: str, count: int = 60, timeframe: str = "1 day"):
            return bars_by_sym.get(symbol, [])[-count:]

        broker.get_historical_bars = _fake_hist  # type: ignore[method-assign]
        dm = DualMomentumRotationStrategy(
            bot_id="dm-test",
            params={
                "symbols": ["SPY", "TLT"],
                "top_n": 1,
                "lookback_days": 63,
                "trend_sma": 100,
                "defensive_symbol": "TLT",
                "max_capital": 2_000.0,
            },
        )
        submitted: list[object] = []

        async def _submit(req: object) -> None:
            submitted.append(req)

        def _log(level: str, category: str, message: str) -> None:
            return None

        msg = await dm.evaluate(broker, _submit, _log)  # type: ignore[arg-type]
        assert submitted and submitted[0].symbol == "SPY" and submitted[0].side == "BUY"  # type: ignore[attr-defined]
        # Last SPY close = 100 + 249*0.2 = 149.8 -> 2000 // 149.8 = 13 shares (not 66 from $10k)
        assert submitted[0].quantity == 13  # type: ignore[attr-defined]
        assert "Rebalanced" in msg and "SPY" in msg

    asyncio.run(_run())


def test_bot_budget_api_patch_and_persistence(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from app import config
    from app.main import create_app

    db_path = tmp_path / "budget_test.db"
    monkeypatch.setenv("IBKR_DB_PATH", str(db_path))
    config.get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as client:
            resp = client.patch(
                "/api/bots/universe-dual-momentum/budget",
                json={"max_allocation_pct": 40, "max_capital": 3000},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["params"]["max_allocation_pct"] == 40.0
            assert data["params"]["max_capital"] == 3000.0

            # Clear caps
            resp2 = client.patch(
                "/api/bots/universe-dual-momentum/budget",
                json={"max_allocation_pct": None, "max_capital": None},
            )
            assert resp2.status_code == 200
            data2 = resp2.json()
            assert "max_allocation_pct" not in data2["params"]
            assert "max_capital" not in data2["params"]

            # Any bot dry-run preview returns signal & orders without placing trades
            bots_before = {b["name"]: b["trades_placed"] for b in client.get("/api/bots").json()}
            first_bot = next(b for b in bots_before if b != "universe-dual-momentum")
            prev_resp = client.get(f"/api/bots/{first_bot}/preview")
            assert prev_resp.status_code == 200
            prev_data = prev_resp.json()
            assert prev_data["bot_id"] == first_bot
            assert "signal" in prev_data and "orders" in prev_data
            bots_after = {b["name"]: b["trades_placed"] for b in client.get("/api/bots").json()}
            assert bots_after[first_bot] == bots_before[first_bot]

            # Deleting a default bot persists across restarts (not re-seeded)
            del_resp = client.delete(f"/api/bots/{first_bot}")
            assert del_resp.status_code == 200
        app2 = create_app()
        with TestClient(app2) as client2:
            remaining_ids = {b["name"] for b in client2.get("/api/bots").json()}
            assert first_bot not in remaining_ids
    finally:
        config.get_settings.cache_clear()


def test_allow_short_signals_and_backtest() -> None:
    # 1. RSI2 downtrend bounce short + cover
    bars = [_make_bar(f"2026-11-{i+1:02d}", 200.0 - i * 2.0) for i in range(25)]
    # Sharp 2-day bounce still below SMA(20) (~171)
    bars.append(_make_bar("2026-11-26", 160.0))
    bars.append(_make_bar("2026-11-27", 168.0))
    act_off, _ = rsi2_swing_signal(
        bars, has_position=False, params={"trend_sma": 20, "exit_sma": 5, "allow_short": False}
    )
    assert act_off == "HOLD"
    act_short, _ = rsi2_swing_signal(
        bars, has_position=False, params={"trend_sma": 20, "exit_sma": 5, "allow_short": True}
    )
    assert act_short == "SHORT"
    bars.append(_make_bar("2026-11-28", 150.0))
    act_cover, _ = rsi2_swing_signal(
        bars,
        has_position=True,
        params={"trend_sma": 20, "exit_sma": 5, "allow_short": True},
        position_side="SHORT",
    )
    assert act_cover == "COVER"

    # 2. VWAP RSI overbought short + backtest execution
    vwap_bars = [_make_bar(f"2026-11-11 10:{i:02d}", 100.0) for i in range(18)]
    vwap_bars.append(_make_bar("2026-11-11 10:18", 104.0))
    vwap_bars.append(_make_bar("2026-11-11 10:19", 108.0))
    v_short, _, _ = vwap_rsi_signal(
        vwap_bars,
        has_position=False,
        params={"rsi_period": 14, "rsi_overbought": 65, "vwap_dev_pct": 0.4, "allow_short": True},
    )
    assert v_short == "SHORT"
    vwap_bars.append(_make_bar("2026-11-11 10:20", 99.0))
    bt = run_backtest(
        vwap_bars,
        BacktestRequest(
            strategy_type="vwap_rsi_reversion",
            symbol="SPY",
            timeframe="5 mins",
            initial_capital=10_000.0,
            quantity=10,
            stop_loss_pct=None,
            take_profit_pct=None,
            params={"rsi_period": 14, "rsi_overbought": 65, "vwap_dev_pct": 0.4, "allow_short": True},
        ),
    )
    assert bt.total_trades >= 1
    assert any(t.side == "SELL" and t.pnl > 0 for t in bt.trades)


def test_dual_momentum_multi_horizon_blends() -> None:
    from datetime import date, timedelta

    start = date(2024, 1, 1)
    dates = [(start + timedelta(days=i)).isoformat() for i in range(300)]
    bars_by_sym = {
        "SPY": [_make_bar(d, 100.0 + i * 0.20) for i, d in enumerate(dates)],
        "NVDA": [_make_bar(d, 100.0 + i * 0.50) for i, d in enumerate(dates)],
        "IWM": [_make_bar(d, 200.0 - i * 0.25) for i, d in enumerate(dates)],
        "SHY": [_make_bar(d, 80.0 + i * 0.01) for i, d in enumerate(dates)],
    }
    idx_map = {sym: len(bars) - 1 for sym, bars in bars_by_sym.items()}
    for mode in ("blend_3_6_12", "blend_1_3_6_12"):
        targets, _ = compute_dual_momentum_targets(
            bars_by_sym,
            idx_map,
            top_n=2,
            trend_sma=150,
            defensive_symbol="SHY",
            momentum_mode=mode,
        )
        assert "NVDA" in targets and "SPY" in targets
        assert "IWM" not in targets

        bt = run_rotation_backtest(
            bars_by_sym,
            RotationBacktestRequest(
                symbols=["SPY", "NVDA", "IWM", "SHY"],
                defensive_symbol="SHY",
                top_n=2,
                trend_sma=150,
                momentum_mode=mode,  # type: ignore[arg-type]
                initial_capital=10_000.0,
            ),
        )
        assert bt.total_return_pct > 0

    # Volatility-adjusted mode gives higher weight to lower-vol asset
    vol_bars = {
        "LOWVOL": [_make_bar(d, 100.0 + i * 0.25) for i, d in enumerate(dates)],
        "HIGHVOL": [
            _make_bar(d, 100.0 + i * 0.25 + (3.0 if i % 2 == 0 else -3.0))
            for i, d in enumerate(dates)
        ],
        "SHY": [_make_bar(d, 80.0 + i * 0.01) for i, d in enumerate(dates)],
    }
    vol_idx = {sym: len(bars) - 1 for sym, bars in vol_bars.items()}
    vol_targets, _ = compute_dual_momentum_targets(
        vol_bars,
        vol_idx,
        top_n=2,
        lookback_days=63,
        trend_sma=100,
        defensive_symbol="SHY",
        volatility_adjusted=True,
    )
    assert vol_targets["LOWVOL"] > vol_targets["HIGHVOL"]







