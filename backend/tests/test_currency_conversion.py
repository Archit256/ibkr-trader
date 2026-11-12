"""Tests for currency conversion features (SGD to USD, FX rates, and balance tracking)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.broker.base import BrokerError
from app.broker.ibkr_client import IBKRClient
from app.broker.paper_simulator import PaperSimulator
from app.config import Settings
from app.main import create_app
from app.models import CurrencyConversionRequest
from app.risk.guard import RiskViolation
from app.service import TradingService


@pytest.mark.asyncio
async def test_simulator_initial_balances_and_fx_rate() -> None:
    sim = PaperSimulator(initial_cash=100_000.0, seed=42)
    acct = await sim.get_account_summary()
    assert acct.currency_balances.get("USD") == 100_000.0
    assert acct.currency_balances.get("SGD") == 50_000.0

    fx = await sim.get_fx_rate("USDSGD")
    assert fx.pair == "USDSGD"
    assert fx.base_currency == "USD"
    assert fx.quote_currency == "SGD"
    assert fx.rate > 0.0
    assert fx.bid > 0.0
    assert fx.ask >= fx.bid
    assert round(fx.inverse_rate * fx.rate, 2) == 1.0


@pytest.mark.asyncio
async def test_simulator_convert_sgd_to_usd() -> None:
    sim = PaperSimulator(initial_cash=100_000.0, seed=42)
    fx = await sim.get_fx_rate("USDSGD")
    rate = fx.rate

    # Convert 10,000 SGD to USD
    req = CurrencyConversionRequest(
        from_currency="SGD",
        to_currency="USD",
        amount=10_000.0,
        amount_currency="SGD",
        order_type="MKT",
        exchange="IDEALPRO",
    )
    res = await sim.convert_currency(req)
    assert res.success is True
    assert res.from_currency == "SGD"
    assert res.to_currency == "USD"
    assert res.from_amount == 10_000.0
    assert res.to_amount == round(10_000.0 / rate, 2)
    assert res.status == "Filled"

    acct = await sim.get_account_summary()
    assert acct.currency_balances["SGD"] == 40_000.0
    assert acct.currency_balances["USD"] == round(100_000.0 + res.to_amount, 2)
    assert acct.cash == acct.currency_balances["USD"]

    # Verify order was recorded in orders list
    orders = await sim.get_orders()
    assert len(orders) >= 1
    fx_order = orders[0]
    assert fx_order.symbol == "USD.SGD"
    assert fx_order.side == "BUY"
    assert fx_order.source == "fx:convert"


@pytest.mark.asyncio
async def test_simulator_convert_by_specifying_target_usd() -> None:
    sim = PaperSimulator(initial_cash=50_000.0, seed=42)
    fx = await sim.get_fx_rate("USDSGD")
    rate = fx.rate

    # Specify target: I want 5,000 USD
    req = CurrencyConversionRequest(
        from_currency="SGD",
        to_currency="USD",
        amount=5_000.0,
        amount_currency="USD",
        order_type="MKT",
        exchange="FXCONV",
    )
    res = await sim.convert_currency(req)
    assert res.success is True
    assert res.to_amount == 5_000.0
    assert res.from_amount == round(5_000.0 * rate, 2)

    acct = await sim.get_account_summary()
    assert acct.currency_balances["SGD"] == round(50_000.0 - res.from_amount, 2)
    assert acct.currency_balances["USD"] == 55_000.0


@pytest.mark.asyncio
async def test_simulator_rejects_insufficient_sgd() -> None:
    sim = PaperSimulator(initial_cash=10_000.0, seed=42)
    req = CurrencyConversionRequest(
        from_currency="SGD",
        to_currency="USD",
        amount=999_999.0,
        amount_currency="SGD",
    )
    with pytest.raises(BrokerError, match="Insufficient SGD balance"):
        await sim.convert_currency(req)


@pytest.mark.asyncio
async def test_service_kill_switch_blocks_fx_conversion(tmp_path) -> None:
    svc = TradingService(Settings(db_path=str(tmp_path / "trading.db")))
    await svc.connect()

    # Engaged kill switch should block conversion
    await svc.set_kill_switch(True, reason="Emergency market halt")
    req = CurrencyConversionRequest(
        from_currency="SGD",
        to_currency="USD",
        amount=1_000.0,
        amount_currency="SGD",
    )
    with pytest.raises(RiskViolation, match="Trading is halted"):
        await svc.convert_currency(req)

    # Disengaging kill switch allows conversion
    await svc.set_kill_switch(False)
    res = await svc.convert_currency(req)
    assert res.success is True
    assert res.from_amount == 1_000.0


@pytest.mark.asyncio
async def test_ibkr_client_live_guard_blocks_unauthorized_conversion() -> None:
    # Port 7496 is a live TWS port
    client = IBKRClient(port=7496, allow_live_trading=False)
    req = CurrencyConversionRequest(
        from_currency="SGD",
        to_currency="USD",
        amount=1_000.0,
        amount_currency="SGD",
    )
    with pytest.raises(BrokerError, match="ALLOW_LIVE_TRADING is false"):
        # Even without full network connect, is_live check protects live orders
        client._connected = True
        await client.convert_currency(req)


def test_api_fx_rate_and_convert_endpoints(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "fx_test.db"))
    from app import config

    config.get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as client:
            # 1. Fetch FX Rate
            rate_res = client.get("/api/fx/rate?pair=USDSGD")
            assert rate_res.status_code == 200
            fx_data = rate_res.json()
            assert fx_data["pair"] == "USDSGD"
            assert fx_data["rate"] > 0.0
            assert fx_data["inverse_rate"] > 0.0

            # 2. Check initial account balances
            acct_res = client.get("/api/account")
            assert acct_res.status_code == 200
            acct = acct_res.json()
            assert "currency_balances" in acct
            initial_sgd = acct["currency_balances"]["SGD"]
            initial_usd = acct["currency_balances"]["USD"]

            # 3. Convert SGD to USD via API
            conv_res = client.post(
                "/api/fx/convert",
                json={
                    "from_currency": "SGD",
                    "to_currency": "USD",
                    "amount": 2_500.0,
                    "amount_currency": "SGD",
                    "order_type": "MKT",
                    "exchange": "IDEALPRO",
                },
            )
            assert conv_res.status_code == 200
            conv_data = conv_res.json()
            assert conv_data["success"] is True
            assert conv_data["from_amount"] == 2_500.0
            assert conv_data["to_amount"] > 0.0

            # 4. Check updated balances
            acct_after = client.get("/api/account").json()
            assert acct_after["currency_balances"]["SGD"] == initial_sgd - 2_500.0
            assert acct_after["currency_balances"]["USD"] == round(
                initial_usd + conv_data["to_amount"], 2
            )
    finally:
        config.get_settings.cache_clear()
