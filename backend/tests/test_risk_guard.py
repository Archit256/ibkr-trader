"""Unit tests for pre-trade RiskGuard checks."""

import pytest

from app.models import AccountSummary, OrderRequest, Position, RiskLimits
from app.risk.guard import RiskGuard, RiskViolation


def _make_guard() -> RiskGuard:
    return RiskGuard(
        RiskLimits(
            max_order_qty=100,
            max_order_notional=10_000.0,
            max_position_shares=150,
            max_daily_loss=1_000.0,
            allow_live_trading=False,
        )
    )


def _account(daily_pnl: float = 0.0) -> AccountSummary:
    return AccountSummary(
        account_id="TEST",
        net_liquidation=100_000.0,
        cash=100_000.0,
        buying_power=200_000.0,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
        daily_pnl=daily_pnl,
    )


def test_allows_valid_order() -> None:
    guard = _make_guard()
    order = OrderRequest(symbol="AAPL", side="BUY", quantity=20, order_type="MKT")
    guard.validate_order(order, reference_price=200.0, positions=[], account=_account())


def test_rejects_excess_quantity() -> None:
    guard = _make_guard()
    order = OrderRequest(symbol="AAPL", side="BUY", quantity=101, order_type="MKT")
    with pytest.raises(RiskViolation, match="max_order_qty"):
        guard.validate_order(order, reference_price=50.0, positions=[], account=_account())


def test_rejects_excess_notional() -> None:
    guard = _make_guard()
    order = OrderRequest(symbol="NVDA", side="BUY", quantity=60, order_type="MKT")
    with pytest.raises(RiskViolation, match="max_order_notional"):
        guard.validate_order(order, reference_price=200.0, positions=[], account=_account())


def test_rejects_projected_position_limit() -> None:
    guard = _make_guard()
    positions = [
        Position(
            symbol="AAPL",
            quantity=120,
            avg_cost=200.0,
            market_price=205.0,
            market_value=24_600.0,
            unrealized_pnl=600.0,
        )
    ]
    order = OrderRequest(symbol="AAPL", side="BUY", quantity=40, order_type="MKT")
    with pytest.raises(RiskViolation, match="max_position_shares"):
        guard.validate_order(order, reference_price=205.0, positions=positions, account=_account())


def test_trips_daily_loss_circuit_breaker() -> None:
    guard = _make_guard()
    order = OrderRequest(symbol="AAPL", side="BUY", quantity=5, order_type="MKT")
    with pytest.raises(RiskViolation, match="Daily loss limit"):
        guard.validate_order(
            order, reference_price=200.0, positions=[], account=_account(daily_pnl=-1500.0)
        )
    assert guard.trading_halted is True


def test_kill_switch_blocks_and_resumes() -> None:
    guard = _make_guard()
    guard.set_kill_switch(True, "Emergency stop")
    order = OrderRequest(symbol="AAPL", side="BUY", quantity=5, order_type="MKT")
    with pytest.raises(RiskViolation, match="Emergency stop"):
        guard.validate_order(order, reference_price=200.0, positions=[], account=_account())

    guard.set_kill_switch(False)
    guard.validate_order(order, reference_price=200.0, positions=[], account=_account())

