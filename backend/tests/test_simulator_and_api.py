"""Integration tests for PaperSimulator and FastAPI endpoints."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.broker.ibkr_client import IBKRClient
from app.broker.base import BrokerError
from app.main import create_app


def test_api_trading_lifecycle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "lifecycle.db"))
    from app import config

    config.get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        # Health check
        res = client.get("/api/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

        # Initial snapshot in simulator mode
        snap = client.get("/api/snapshot").json()
        assert snap["connection"]["broker_mode"] == "simulator"
        assert snap["connection"]["connected"] is True
        assert snap["account"]["cash"] == 100000.0

        # Place market BUY order
        order_res = client.post(
            "/api/orders",
            json={
                "symbol": "AAPL",
                "side": "BUY",
                "quantity": 10,
                "order_type": "MKT",
                "tif": "DAY",
            },
        )
        assert order_res.status_code == 200
        order_data = order_res.json()
        assert order_data["status"] == "Filled"
        assert order_data["filled_quantity"] == 10

        # Position created
        positions = client.get("/api/positions").json()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "AAPL"
        assert positions[0]["quantity"] == 10

        # Place resting limit BUY well below market and cancel it
        lmt_res = client.post(
            "/api/orders",
            json={
                "symbol": "AAPL",
                "side": "BUY",
                "quantity": 5,
                "order_type": "LMT",
                "limit_price": 10.0,
                "tif": "GTC",
            },
        )
        assert lmt_res.status_code == 200
        lmt_order = lmt_res.json()
        assert lmt_order["status"] == "Submitted"

        cancel_res = client.delete(f"/api/orders/{lmt_order['order_id']}")
        assert cancel_res.status_code == 200
        assert cancel_res.json()["status"] == "Cancelled"

        # Kill switch blocks new orders
        ks = client.post(
            "/api/risk/kill-switch",
            json={"halted": True, "cancel_open_orders": True, "reason": "Test halt"},
        )
        assert ks.status_code == 200
        assert ks.json()["trading_halted"] is True

        blocked = client.post(
            "/api/orders",
            json={"symbol": "MSFT", "side": "BUY", "quantity": 5, "order_type": "MKT"},
        )
        assert blocked.status_code == 400
        assert "Test halt" in blocked.json()["detail"]

        # Resume trading and start/stop strategy
        client.post("/api/risk/kill-switch", json={"halted": False})
        strat = client.post(
            "/api/strategies/sma_crossover/start",
            json={"symbol": "NVDA", "params": {"fast_period": 4, "slow_period": 12, "quantity": 5}},
        )
        assert strat.status_code == 200
        assert strat.json()["running"] is True
        assert strat.json()["symbol"] == "NVDA"

        stopped = client.post("/api/strategies/sma_crossover/stop")
        assert stopped.status_code == 200
        assert stopped.json()["running"] is False


async def test_ibkr_blocks_live_port_by_default() -> None:
    ib_client = IBKRClient(host="127.0.0.1", port=7496, client_id=1, allow_live_trading=False)
    try:
        await ib_client.connect()
        assert False, "Expected BrokerError when connecting to live port without opt-in"
    except BrokerError as exc:
        assert "LIVE IBKR port" in str(exc)

