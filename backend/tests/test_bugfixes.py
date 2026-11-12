"""Regression tests for bug fixes across preview_bot, shock_reversion, NewsGuard, RiskGuard, simulator, and storage."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.broker.paper_simulator import PaperSimulator
from app.config import Settings
from app.market.news_guard import classify_headlines
from app.models import OrderRequest, Position, PriceBar, RiskLimits
from app.risk.guard import RiskGuard, RiskViolation
from app.service import TradingService
from app.storage.db import TradingStore
from app.strategies.shock_reversion import shock_reversion_signal


async def test_preview_bot_captures_order_without_mutating_state(tmp_path) -> None:
    svc = TradingService(Settings(db_path=str(tmp_path / "test.db")))
    await svc.connect()

    # Build bars that trigger vwap_rsi_reversion BUY on the latest bar:
    # steady prices then a sharp drop so close < VWAP and RSI <= 30.
    bars: list[PriceBar] = []
    for i in range(25):
        close = 100.0 if i < 20 else 100.0 - (i - 19) * 3.0
        bars.append(
            PriceBar(
                time=f"2026-11-12 10:{i:02d}",
                open=close + 0.5,
                high=close + 1.0,
                low=close - 0.5,
                close=close,
                volume=50_000,
            )
        )
    svc._simulator._bars["SPY"] = bars
    svc._simulator._prices["SPY"] = bars[-1].close

    # Bypass external yfinance call in NewsGuard during unit test
    async def _clear_news(symbol: str, force_refresh: bool = False):
        from app.models import NewsCheckResult

        return NewsCheckResult(symbol=symbol, blocked=False, reason="clear")

    svc.check_news = _clear_news  # type: ignore[method-assign]

    bot = svc._strategies._strategies["spy-vwap-reversion"]
    assert bot.trades_placed == 0
    assert bot._last_entry_monotonic == 0.0

    preview = await svc.preview_bot("spy-vwap-reversion")
    assert len(preview.orders) == 1
    assert preview.orders[0].symbol == "SPY"
    assert preview.orders[0].side == "BUY"
    assert preview.orders[0].risk_status == "PASS"

    # Dry-run preview must not mutate bot state
    assert bot.trades_placed == 0
    assert bot._last_entry_monotonic == 0.0


def test_shock_reversion_waits_for_large_lookback() -> None:
    bars = [
        PriceBar(
            time=f"2026-11-12 10:{i:02d}",
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=10_000,
        )
        for i in range(25)
    ]
    action, detail, _, _ = shock_reversion_signal(
        bars, has_position=False, params={"shock_lookback": 25}
    )
    assert action == "HOLD"
    assert "Waiting for bars" in detail


def test_classify_headlines_handles_naive_iso_timestamp() -> None:
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    raw = [
        {
            "title": "NVDA reports earnings beat",
            "providerPublishTime": "2026-11-12T10:30:00",
        }
    ]
    headlines, reason = classify_headlines(raw, now=now, lookback_hours=12.0)
    assert len(headlines) == 1
    assert reason is not None
    assert "earnings" in reason


def test_risk_guard_allows_reducing_oversized_position() -> None:
    guard = RiskGuard(
        RiskLimits(
            max_order_qty=100,
            max_order_notional=50_000.0,
            max_position_shares=150,
            max_daily_loss=2_000.0,
            allow_live_trading=False,
        )
    )
    positions = [
        Position(
            symbol="AAPL",
            quantity=200.0,
            avg_cost=100.0,
            market_price=100.0,
            market_value=20_000.0,
            unrealized_pnl=0.0,
        )
    ]

    # Reducing an oversized position (200 -> 180) must be allowed
    reduce_order = OrderRequest(symbol="AAPL", side="SELL", quantity=20, order_type="MKT")
    guard.validate_order(reduce_order, reference_price=100.0, positions=positions, account=None)

    # Increasing an oversized position (200 -> 210) must still be blocked
    increase_order = OrderRequest(symbol="AAPL", side="BUY", quantity=10, order_type="MKT")
    with pytest.raises(RiskViolation, match="max_position_shares"):
        guard.validate_order(increase_order, reference_price=100.0, positions=positions, account=None)


async def test_simulator_get_historical_bars_zero_or_negative_count() -> None:
    sim = PaperSimulator(seed=1)
    assert await sim.get_historical_bars("SPY", count=0) == []
    assert await sim.get_historical_bars("SPY", count=-5) == []


def test_store_load_historical_bars_returns_latest_limit(tmp_path) -> None:
    store = TradingStore(tmp_path / "bars.db")
    bars = [
        PriceBar(
            time=f"2026-11-0{i + 1}",
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1000,
        )
        for i in range(5)
    ]
    store.upsert_historical_bars("SPY", "1 day", bars, "yfinance")
    loaded = store.load_historical_bars("SPY", "1 day", limit=3)
    assert [b.time for b in loaded] == ["2026-11-03", "2026-11-04", "2026-11-05"]
