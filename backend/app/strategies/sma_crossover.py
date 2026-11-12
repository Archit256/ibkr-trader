"""Simple Moving Average (SMA) crossover strategy with bracket stop-loss support."""

from __future__ import annotations

from typing import Any

from app.broker.base import BrokerClient
from app.models import OrderRequest, PriceBar, Timeframe
from app.strategies.base import ActivityLogger, BaseStrategy, NewsChecker, OrderSubmitter
from app.strategies.indicators import sma


def sma_crossover_signal(
    bars: list[PriceBar],
    params: dict[str, Any],
) -> tuple[str, str]:
    """Return (regime, detail) where regime is 'BULL', 'BEAR', or 'WAIT'."""
    fast = max(2, int(params.get("fast_period", 5)))
    slow = max(fast + 1, int(params.get("slow_period", 15)))
    if len(bars) < slow:
        return "WAIT", f"Waiting for bars ({len(bars)}/{slow})"

    closes = [b.close for b in bars]
    fast_sma = sma(closes, fast)
    slow_sma = sma(closes, slow)
    if fast_sma is None or slow_sma is None:
        return "WAIT", "Insufficient bars"

    regime = "BULL" if fast_sma > slow_sma else "BEAR"
    return regime, f"SMA{fast}={fast_sma:.2f}, SMA{slow}={slow_sma:.2f}"


class SmaCrossoverStrategy(BaseStrategy):
    """Buys when fast SMA crosses above slow SMA; flattens/sells on cross below."""

    strategy_type = "sma_crossover"
    description = (
        "Evaluates fast vs. slow SMA on recent bars and routes risk-checked bracket orders on crossovers."
    )

    def __init__(
        self,
        bot_id: str = "sma_crossover",
        symbol: str = "NVDA",
        timeframe: Timeframe = "5 mins",
        stop_loss_pct: float | None = 1.5,
        take_profit_pct: float | None = 3.0,
        params: dict[str, Any] | None = None,
    ) -> None:
        default_params: dict[str, Any] = {
            "fast_period": 5,
            "slow_period": 15,
            "quantity": 10,
        }
        if params:
            default_params.update(params)
        super().__init__(
            bot_id=bot_id,
            symbol=symbol,
            timeframe=timeframe,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            params=default_params,
        )
        self._last_regime: str | None = None

    async def evaluate(
        self,
        broker: BrokerClient,
        submit_order: OrderSubmitter,
        log_activity: ActivityLogger,
        news_checker: NewsChecker | None = None,
    ) -> str:
        slow = max(3, int(self.params.get("slow_period", 15)))
        qty = max(1, int(self.params.get("quantity", 10)))

        bars = await broker.get_historical_bars(
            self.symbol, count=slow + 5, timeframe=self.timeframe
        )
        regime, detail = sma_crossover_signal(bars, self.params)
        if regime == "WAIT":
            return detail

        if self._last_regime is None:
            self._last_regime = regime
            return f"Initialized {regime} ({detail})"

        if regime != self._last_regime:
            positions = await broker.get_positions()
            cur_qty = next((p.quantity for p in positions if p.symbol == self.symbol), 0.0)
            self._last_regime = regime

            if regime == "BULL" and cur_qty <= 0:
                if self.is_in_cooldown():
                    return f"Cooldown active ({self.cooldown_seconds}s) — skipped BULL cross"
                last_price = bars[-1].close
                qty = await self.resolve_entry_quantity(broker, last_price, qty)
                if qty <= 0:
                    return f"Skipped BUY {self.symbol}: price ${last_price:.2f} exceeds bot budget"
                order = self.build_entry_order(
                    side="BUY",
                    quantity=qty,
                    ref_price=last_price,
                )
                await submit_order(order)
                self.mark_entry_placed()
                self.trades_placed += 1
                log_activity(
                    "success",
                    "strategy",
                    f"[{self.name}] Bullish cross on {self.symbol}: BUY {qty}",
                )
                return f"BUY {qty} {self.symbol} ({detail})"

            if regime == "BEAR" and cur_qty > 0:
                sell_qty = int(abs(cur_qty))
                order = self.build_order(side="SELL", quantity=sell_qty, ref_price=bars[-1].close)
                await submit_order(order)
                self.trades_placed += 1
                log_activity(
                    "warning",
                    "strategy",
                    f"[{self.name}] Bearish cross on {self.symbol}: SELL {sell_qty}",
                )
                return f"SELL {sell_qty} {self.symbol} ({detail})"

        return f"{regime} hold ({detail})"
