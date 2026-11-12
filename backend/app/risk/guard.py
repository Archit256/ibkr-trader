"""Pre-trade risk guardrails and kill-switch enforcement."""

from __future__ import annotations

from app.models import (
    AccountSummary,
    OrderRequest,
    Position,
    RiskLimits,
    RiskLimitsPatch,
    RiskStatus,
)


class RiskViolation(Exception):
    """Raised when an order violates pre-trade risk rules."""


class RiskGuard:
    """Validates orders against position, notional, daily-loss, and kill-switch rules."""

    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits
        self._trading_halted = False
        self._halt_reason: str | None = None

    @property
    def trading_halted(self) -> bool:
        return self._trading_halted

    def status(self) -> RiskStatus:
        return RiskStatus(
            trading_halted=self._trading_halted,
            halt_reason=self._halt_reason,
            limits=self._limits,
        )

    def set_kill_switch(self, halted: bool, reason: str | None = None) -> RiskStatus:
        self._trading_halted = halted
        self._halt_reason = (reason or "Manual kill switch activated") if halted else None
        return self.status()

    def update_limits(self, patch: RiskLimitsPatch) -> RiskStatus:
        data = self._limits.model_dump()
        for key, value in patch.model_dump(exclude_none=True).items():
            data[key] = value
        self._limits = RiskLimits(**data)
        return self.status()

    def validate_order(
        self,
        order: OrderRequest,
        reference_price: float | None,
        positions: list[Position],
        account: AccountSummary | None,
    ) -> None:
        if self._trading_halted:
            raise RiskViolation(
                f"Trading is halted ({self._halt_reason or 'kill switch active'})."
            )

        if account is not None and account.daily_pnl <= -self._limits.max_daily_loss:
            self._trading_halted = True
            self._halt_reason = (
                f"Daily loss limit reached (${account.daily_pnl:,.2f} <= "
                f"-${self._limits.max_daily_loss:,.2f})"
            )
            raise RiskViolation(self._halt_reason)

        if order.quantity > self._limits.max_order_qty:
            raise RiskViolation(
                f"Order qty {order.quantity} exceeds max_order_qty ({self._limits.max_order_qty})."
            )

        est_price = order.limit_price or order.stop_price or reference_price
        if est_price is not None:
            notional = order.quantity * est_price
            if notional > self._limits.max_order_notional:
                raise RiskViolation(
                    f"Order notional ${notional:,.2f} exceeds max_order_notional "
                    f"(${self._limits.max_order_notional:,.2f})."
                )

        current_qty = next(
            (p.quantity for p in positions if p.symbol == order.symbol), 0.0
        )
        signed_qty = order.quantity if order.side == "BUY" else -order.quantity
        projected_abs_qty = abs(current_qty + signed_qty)
        if projected_abs_qty > abs(current_qty) and projected_abs_qty > self._limits.max_position_shares:
            raise RiskViolation(
                f"Projected position ({projected_abs_qty:.0f} shares) for {order.symbol} "
                f"exceeds max_position_shares ({self._limits.max_position_shares})."
            )

