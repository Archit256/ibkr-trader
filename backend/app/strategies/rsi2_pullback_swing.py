"""Daily 2-Period RSI Pullback Swing strategy for liquid US ETFs and mega-cap stocks."""

from __future__ import annotations

from typing import Any

from app.broker.base import BrokerClient
from app.models import OrderRequest, PriceBar, Timeframe
from app.strategies.base import ActivityLogger, BaseStrategy, NewsChecker, OrderSubmitter
from app.strategies.indicators import sma, wilder_rsi


def rsi2_swing_signal(
    bars: list[PriceBar],
    has_position: bool,
    params: dict[str, Any],
    position_side: str | None = None,
) -> tuple[str, str]:
    """Pure signal evaluator shared by live bot and backtester.

    Returns (action, detail) where action in {"BUY", "EXIT", "SHORT", "COVER", "HOLD"}.
    """
    rsi_period = max(2, int(params.get("rsi_period", 2)))
    rsi_buy = float(params.get("rsi_buy", 12.0))
    rsi_short = float(params.get("rsi_short", 88.0))
    allow_short = bool(params.get("allow_short", False))
    trend_period = max(20, int(params.get("trend_sma", 200)))
    exit_period = max(2, int(params.get("exit_sma", 5)))

    needed = max(trend_period, exit_period, rsi_period + 2)
    if len(bars) < needed:
        return "HOLD", f"Waiting for bars ({len(bars)}/{needed})"

    closes = [b.close for b in bars]
    trend_val = sma(closes, trend_period)
    exit_val = sma(closes, exit_period)
    rsi_val = wilder_rsi(closes, rsi_period)
    if trend_val is None or exit_val is None or rsi_val is None:
        return "HOLD", "Insufficient indicator data"

    last_close = closes[-1]
    side = position_side or ("LONG" if has_position else "FLAT")

    if side == "FLAT":
        if last_close > trend_val and rsi_val <= rsi_buy:
            return (
                "BUY",
                f"Uptrend dip (Close={last_close:.2f} > SMA{trend_period}={trend_val:.2f}, RSI2={rsi_val:.1f})",
            )
        if allow_short and last_close < trend_val and rsi_val >= rsi_short:
            return (
                "SHORT",
                f"Downtrend bounce (Close={last_close:.2f} < SMA{trend_period}={trend_val:.2f}, RSI2={rsi_val:.1f})",
            )
    elif side == "LONG":
        if last_close >= exit_val:
            return (
                "EXIT",
                f"Bounce exit (Close={last_close:.2f} >= SMA{exit_period}={exit_val:.2f})",
            )
    elif side == "SHORT":
        if last_close <= exit_val:
            return (
                "COVER",
                f"Pullback cover (Close={last_close:.2f} <= SMA{exit_period}={exit_val:.2f})",
            )

    return (
        "HOLD",
        f"Close={last_close:.2f} SMA{trend_period}={trend_val:.2f} SMA{exit_period}={exit_val:.2f} RSI{rsi_period}={rsi_val:.1f}",
    )


class Rsi2PullbackSwingStrategy(BaseStrategy):
    """Buys sharp 2-period RSI pullbacks in uptrends and exits on bounce above short SMA."""

    strategy_type = "rsi2_pullback_swing"
    description = (
        "Daily Swing: buys sharp 2-period RSI dips when above long-term trend SMA, with a server-side stop-loss."
    )

    def __init__(
        self,
        bot_id: str = "qqq-rsi2-swing",
        symbol: str = "QQQ",
        timeframe: Timeframe = "1 day",
        stop_loss_pct: float | None = 3.0,
        take_profit_pct: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        default_params: dict[str, Any] = {
            "rsi_period": 2,
            "rsi_buy": 12,
            "trend_sma": 200,
            "exit_sma": 5,
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

    async def evaluate(
        self,
        broker: BrokerClient,
        submit_order: OrderSubmitter,
        log_activity: ActivityLogger,
        news_checker: NewsChecker | None = None,
    ) -> str:
        qty = max(1, int(self.params.get("quantity", 10)))
        trend_period = max(20, int(self.params.get("trend_sma", 200)))
        bars = await broker.get_historical_bars(
            self.symbol, count=trend_period + 10, timeframe=self.timeframe
        )
        positions = await broker.get_positions()
        cur_qty = next((p.quantity for p in positions if p.symbol == self.symbol), 0.0)
        pos_side = "LONG" if cur_qty > 0 else ("SHORT" if cur_qty < 0 else "FLAT")

        action, detail = rsi2_swing_signal(
            bars=bars, has_position=cur_qty > 0, params=self.params, position_side=pos_side
        )

        if action in ("BUY", "SHORT") and cur_qty == 0:
            if self.is_in_cooldown():
                return f"Cooldown active ({self.cooldown_seconds}s) — skipped {detail}"

            if action == "BUY":
                veto = await self.check_news_veto(news_checker)
                if veto:
                    msg = f"NewsGuard vetoed BUY {self.symbol}: {veto}"
                    log_activity("warning", "news", f"[{self.name}] {msg}")
                    return msg

            last_price = bars[-1].close
            qty = await self.resolve_entry_quantity(broker, last_price, qty)
            if qty <= 0:
                return f"Skipped {action} {self.symbol}: price ${last_price:.2f} exceeds bot budget"
            order_side = "BUY" if action == "BUY" else "SELL"
            order = self.build_entry_order(
                side=order_side,
                quantity=qty,
                ref_price=last_price,
            )
            await submit_order(order)
            self.mark_entry_placed()
            self.trades_placed += 1
            log_activity("success", "strategy", f"[{self.name}] {action} {qty} {self.symbol}: {detail}")
            return f"{action} {qty} {self.symbol} — {detail}"

        if action == "EXIT" and cur_qty > 0:
            sell_qty = int(abs(cur_qty))
            await submit_order(self.build_order(side="SELL", quantity=sell_qty, ref_price=bars[-1].close))
            self.trades_placed += 1
            log_activity("warning", "strategy", f"[{self.name}] EXIT {sell_qty} {self.symbol}: {detail}")
            return f"SELL {sell_qty} {self.symbol} — {detail}"

        if action == "COVER" and cur_qty < 0:
            cover_qty = int(abs(cur_qty))
            await submit_order(self.build_order(side="BUY", quantity=cover_qty, ref_price=bars[-1].close))
            self.trades_placed += 1
            log_activity("warning", "strategy", f"[{self.name}] COVER {cover_qty} {self.symbol}: {detail}")
            return f"COVER {cover_qty} {self.symbol} — {detail}"

        return detail

