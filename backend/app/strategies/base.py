"""Base strategy abstraction with server-side brackets, cooldowns, auto-flatten, and news veto."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, time as dt_time, timezone
from typing import Any, Awaitable, Callable

from app.broker.base import BrokerClient
from app.market.calendar import ET_ZONE
from app.models import (
    NewsCheckResult,
    OrderRecord,
    OrderRequest,
    OrderSide,
    StrategyInfo,
    StrategyType,
    Timeframe,
    utc_now,
)

OrderSubmitter = Callable[[OrderRequest], Awaitable[OrderRecord]]
ActivityLogger = Callable[[str, str, str], None]
NewsChecker = Callable[[str], Awaitable[NewsCheckResult]]


class BaseStrategy(ABC):
    """Base class for pluggable automated trading bots."""

    strategy_type: StrategyType
    description: str

    def __init__(
        self,
        bot_id: str,
        symbol: str,
        timeframe: Timeframe = "5 mins",
        stop_loss_pct: float | None = 1.5,
        take_profit_pct: float | None = 2.5,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.name = bot_id
        self.symbol = symbol.strip().upper()
        self.timeframe: Timeframe = timeframe
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.params: dict[str, Any] = params or {}
        self.running = False
        self.last_signal: str | None = None
        self.last_checked_at: datetime | None = None
        self.trades_placed: int = 0
        self._last_entry_monotonic: float = 0.0

    def configure(
        self,
        symbol: str | None = None,
        timeframe: Timeframe | None = None,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        if symbol:
            self.symbol = symbol.strip().upper()
        if timeframe:
            self.timeframe = timeframe
        if stop_loss_pct is not None:
            self.stop_loss_pct = stop_loss_pct
        if take_profit_pct is not None:
            self.take_profit_pct = take_profit_pct
        if params:
            self.params.update(params)

    @property
    def cooldown_seconds(self) -> int:
        return max(0, int(self.params.get("cooldown_seconds", 300)))

    def is_in_cooldown(self) -> bool:
        if self._last_entry_monotonic <= 0:
            return False
        return (time.monotonic() - self._last_entry_monotonic) < self.cooldown_seconds

    def mark_entry_placed(self) -> None:
        self._last_entry_monotonic = time.monotonic()

    async def resolve_capital_budget(self, broker: BrokerClient) -> float | None:
        """Return the bot's effective dollar budget from max_allocation_pct and/or max_capital."""
        raw_pct = self.params.get("max_allocation_pct")
        raw_cap = self.params.get("max_capital")
        pct = float(raw_pct) if raw_pct is not None and float(raw_pct) > 0 else None
        cap = float(raw_cap) if raw_cap is not None and float(raw_cap) > 0 else None
        if pct is None and cap is None:
            return None
        budgets: list[float] = []
        if pct is not None:
            acct = await broker.get_account_summary()
            budgets.append(max(0.0, acct.net_liquidation * (min(pct, 100.0) / 100.0)))
        if cap is not None:
            budgets.append(cap)
        return min(budgets) if budgets else None

    async def resolve_entry_quantity(
        self, broker: BrokerClient, ref_price: float, default_qty: int
    ) -> int:
        """Compute entry shares from bot budget caps, or fall back to fixed default_qty."""
        budget = await self.resolve_capital_budget(broker)
        if budget is None:
            return max(1, default_qty)
        if ref_price <= 0:
            return 0
        return max(0, int(budget // ref_price))

    def should_auto_flatten(self, now: datetime | None = None) -> bool:
        """True during 15:55–16:00 ET for intraday bots to prevent overnight holds."""
        if self.timeframe not in ("5 mins", "15 mins"):
            return False
        if not bool(self.params.get("auto_flatten_eod", True)):
            return False
        utc_dt = now or datetime.now(timezone.utc)
        if utc_dt.tzinfo is None:
            utc_dt = utc_dt.replace(tzinfo=timezone.utc)
        et_dt = utc_dt.astimezone(ET_ZONE)
        if et_dt.weekday() >= 5:
            return False
        return dt_time(15, 55) <= et_dt.time() < dt_time(16, 0)

    async def check_news_veto(
        self, news_checker: NewsChecker | None
    ) -> str | None:
        """Return veto reason if NewsGuard flags an earnings blackout or fundamental catalyst."""
        if news_checker is None or not bool(self.params.get("respect_news_guard", True)):
            return None
        res = await news_checker(self.symbol)
        return res.reason if res.blocked else None

    def collared_limit_price(self, side: OrderSide, ref_price: float) -> float | None:
        """Compute a marketable limit price with a protective collar (default 0.25%)."""
        if ref_price <= 0:
            return None
        collar_pct = max(0.01, float(self.params.get("limit_collar_pct", 0.25)))
        mult = 1.0 + (collar_pct / 100.0) if side == "BUY" else 1.0 - (collar_pct / 100.0)
        return round(max(0.01, ref_price * mult), 2)

    def build_order(
        self,
        side: OrderSide,
        quantity: int,
        ref_price: float,
        symbol: str | None = None,
        stop_loss_price: float | None = None,
        take_profit_price: float | None = None,
    ) -> OrderRequest:
        """Build a collared LMT order by default (avoiding uncollared IBKR MKT orders)."""
        req_type = str(self.params.get("order_type", "LMT")).upper()
        lmt_price = self.collared_limit_price(side, ref_price) if req_type != "MKT" else None
        order_type = "LMT" if lmt_price is not None else "MKT"
        return OrderRequest(
            symbol=symbol or self.symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=lmt_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            tif="DAY",
            source=f"strategy:{self.name}",
        )

    def build_entry_order(
        self,
        side: OrderSide,
        quantity: int,
        ref_price: float,
        stop_loss_override: float | None = None,
        take_profit_override: float | None = None,
    ) -> OrderRequest:
        """Build a collared LMT entry order with server-side stop-loss and optional take-profit."""
        sl_price: float | None = stop_loss_override
        tp_price: float | None = take_profit_override

        if sl_price is None and self.stop_loss_pct is not None and self.stop_loss_pct > 0:
            mult = 1.0 - (self.stop_loss_pct / 100.0) if side == "BUY" else 1.0 + (self.stop_loss_pct / 100.0)
            sl_price = round(max(0.01, ref_price * mult), 2)

        if tp_price is None and self.take_profit_pct is not None and self.take_profit_pct > 0:
            mult = 1.0 + (self.take_profit_pct / 100.0) if side == "BUY" else 1.0 - (self.take_profit_pct / 100.0)
            tp_price = round(max(0.01, ref_price * mult), 2)

        return self.build_order(
            side=side,
            quantity=quantity,
            ref_price=ref_price,
            stop_loss_price=sl_price,
            take_profit_price=tp_price,
        )

    def info(self) -> StrategyInfo:
        return StrategyInfo(
            name=self.name,
            strategy_type=self.strategy_type,
            description=self.description,
            running=self.running,
            symbol=self.symbol,
            timeframe=self.timeframe,
            stop_loss_pct=self.stop_loss_pct,
            take_profit_pct=self.take_profit_pct,
            params=dict(self.params),
            last_signal=self.last_signal,
            last_checked_at=self.last_checked_at,
            trades_placed=self.trades_placed,
        )

    @abstractmethod
    async def evaluate(
        self,
        broker: BrokerClient,
        submit_order: OrderSubmitter,
        log_activity: ActivityLogger,
        news_checker: NewsChecker | None = None,
    ) -> str:
        """Run one evaluation cycle and return a short signal summary."""

    async def tick(
        self,
        broker: BrokerClient,
        submit_order: OrderSubmitter,
        log_activity: ActivityLogger,
        news_checker: NewsChecker | None = None,
    ) -> str:
        self.last_checked_at = utc_now()
        if self.should_auto_flatten():
            positions = await broker.get_positions()
            cur_qty = next((p.quantity for p in positions if p.symbol == self.symbol), 0.0)
            if cur_qty != 0:
                flatten_qty = int(abs(cur_qty))
                flatten_side = "SELL" if cur_qty > 0 else "BUY"
                await submit_order(
                    OrderRequest(
                        symbol=self.symbol,
                        side=flatten_side,
                        quantity=flatten_qty,
                        order_type="MKT",
                        tif="DAY",
                        source=f"strategy:{self.name}",
                    )
                )
                self.trades_placed += 1
                msg = f"EOD Auto-Flatten (15:55 ET): {flatten_side} {flatten_qty} {self.symbol}"
                log_activity("warning", "strategy", f"[{self.name}] {msg}")
                self.last_signal = msg
                return msg

        signal = await self.evaluate(broker, submit_order, log_activity, news_checker)
        self.last_signal = signal
        return signal
