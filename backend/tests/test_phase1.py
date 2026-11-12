"""Phase 1 tests: SQLite persistence, US market calendar, bracket stop-losses, and reconciliation."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.broker.ibkr_client import build_ib_orders
from app.broker.paper_simulator import PaperSimulator
from app.config import Settings
from app.main import create_app
from app.market.calendar import USMarketCalendar
from app.models import OrderRecord, OrderRequest
from app.storage.db import TradingStore

ET = ZoneInfo("America/New_York")


def test_us_market_calendar_sessions_and_holidays() -> None:
    cal = USMarketCalendar()

    rth = cal.status(datetime(2026, 11, 11, 10, 30, tzinfo=ET))
    assert rth.is_rth_open is True
    assert rth.session == "RTH"

    pre = cal.status(datetime(2026, 11, 11, 8, 15, tzinfo=ET))
    assert pre.is_rth_open is False
    assert pre.session == "PRE_MARKET"

    after = cal.status(datetime(2026, 11, 11, 17, 0, tzinfo=ET))
    assert after.is_rth_open is False
    assert after.session == "AFTER_HOURS"

    weekend = cal.status(datetime(2026, 11, 14, 11, 0, tzinfo=ET))
    assert weekend.is_rth_open is False
    assert weekend.session == "CLOSED"

    xmas = cal.status(datetime(2026, 12, 25, 11, 0, tzinfo=ET))
    assert xmas.is_rth_open is False
    assert xmas.session == "CLOSED"
    assert "Holiday" in xmas.reason


def test_sqlite_trading_store_persists_across_restarts(tmp_path: Path) -> None:
    db_file = tmp_path / "trading.db"
    store1 = TradingStore(db_file)
    store1.upsert_order(
        OrderRecord(
            order_id=42,
            symbol="SPY",
            side="BUY",
            quantity=10,
            filled_quantity=10,
            order_type="MKT",
            stop_loss_price=580.0,
            take_profit_price=605.0,
            avg_fill_price=592.0,
            status="Filled",
            source="manual",
        ),
        broker_mode="simulator",
    )
    store1.save_strategy_state(
        name="sma_crossover",
        symbol="QQQ",
        running=True,
        params={"fast_period": 5, "slow_period": 15, "quantity": 10},
        last_signal="BULL hold",
        last_checked_at=None,
        trades_placed=2,
    )
    store1.append_activity("info", "test", "Persisted event")

    # Reload from fresh instance
    store2 = TradingStore(db_file)
    orders = store2.list_orders("simulator")
    assert len(orders) == 1
    assert orders[0].order_id == 42
    assert orders[0].stop_loss_price == 580.0

    states = store2.load_strategy_states()
    assert states["sma_crossover"]["symbol"] == "QQQ"
    assert states["sma_crossover"]["trades_placed"] == 2

    events = store2.list_activity()
    assert len(events) == 1
    assert events[0].message == "Persisted event"


async def test_simulator_bracket_stop_loss_and_oca_cancel() -> None:
    sim = PaperSimulator(initial_cash=100_000.0, seed=1)
    sim.set_price("AAPL", 200.0)

    entry = await sim.place_order(
        OrderRequest(
            symbol="AAPL",
            side="BUY",
            quantity=10,
            order_type="MKT",
            stop_loss_price=195.0,
            take_profit_price=210.0,
        )
    )
    assert entry.status == "Filled"

    orders = await sim.get_orders()
    children = [o for o in orders if o.parent_order_id == entry.order_id]
    assert len(children) == 2
    assert all(c.status == "Submitted" for c in children)

    # Drop price to trigger stop-loss
    sim.set_price("AAPL", 194.5)

    positions = await sim.get_positions()
    assert positions == []

    updated = {o.order_type: o for o in await sim.get_orders() if o.parent_order_id == entry.order_id}
    assert updated["STP"].status == "Filled"
    assert updated["LMT"].status == "Cancelled"


def test_build_ib_bracket_orders_links_parent_and_transmit() -> None:
    req = OrderRequest(
        symbol="MSFT",
        side="BUY",
        quantity=5,
        order_type="LMT",
        limit_price=410.0,
        take_profit_price=430.0,
        stop_loss_price=400.0,
    )
    ib_orders = build_ib_orders(req, next_order_id=100)
    assert len(ib_orders) == 3
    parent, tp, sl = ib_orders
    assert parent.orderId == 100
    assert parent.transmit is False
    assert tp.parentId == 100
    assert tp.action == "SELL"
    assert tp.transmit is False
    assert sl.parentId == 100
    assert sl.action == "SELL"
    assert sl.transmit is True


def test_reconcile_and_market_status_endpoints(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "api_test.db"))
    from app import config

    config.get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as client:
            mkt = client.get("/api/market/status")
            assert mkt.status_code == 200
            assert mkt.json()["session"] in ("PRE_MARKET", "RTH", "AFTER_HOURS", "CLOSED")

            rec = client.post("/api/connection/reconcile")
            assert rec.status_code == 200
            assert rec.json()["broker_positions"] == 0

            ord_res = client.post(
                "/api/orders",
                json={
                    "symbol": "SPY",
                    "side": "BUY",
                    "quantity": 5,
                    "order_type": "MKT",
                    "stop_loss_price": 400.0,
                    "take_profit_price": 800.0,
                },
            )
            assert ord_res.status_code == 200
            assert ord_res.json()["stop_loss_price"] == 400.0
    finally:
        config.get_settings.cache_clear()

