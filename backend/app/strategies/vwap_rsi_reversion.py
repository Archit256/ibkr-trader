"""Intraday VWAP + RSI Mean Reversion strategy for liquid US stocks and ETFs."""

from __future__ import annotations

from typing import Any

from app.broker.base import BrokerClient
from app.models import OrderRequest, PriceBar, Timeframe
from app.strategies.base import ActivityLogger, BaseStrategy, NewsChecker, OrderSubmitter
from app.strategies.indicators import rolling_vwap, wilder_rsi


def vwap_rsi_signal(
    bars: list[PriceBar],
    has_position: bool,
    params: dict[str, Any],
    position_side: str | None = None,
) -> tuple[str, str, float | None]:
    """Pure signal evaluator shared by live bot and backtester.

    Returns (action, detail, target_price) where action in {"BUY", "EXIT", "SHORT", "COVER", "HOLD"}.
    """
    rsi_period = max(2, int(params.get("rsi_period", 14)))
    rsi_oversold = float(params.get("rsi_oversold", 30.0))
    rsi_exit = float(params.get("rsi_exit", 55.0))
    rsi_overbought = float(params.get("rsi_overbought", 70.0))
    rsi_cover = float(params.get("rsi_cover", 45.0))
    allow_short = bool(params.get("allow_short", False))
    vwap_dev_pct = float(params.get("vwap_dev_pct", 0.45))

    needed = rsi_period + 2
    if len(bars) < needed:
        return "HOLD", f"Waiting for bars ({len(bars)}/{needed})", None

    closes = [b.close for b in bars]
    rsi_val = wilder_rsi(closes, rsi_period)
    vwap_val = rolling_vwap(bars)
    if rsi_val is None or vwap_val is None or vwap_val <= 0:
        return "HOLD", "Insufficient indicator data", None

    last_close = closes[-1]
    dev_pct = ((last_close - vwap_val) / vwap_val) * 100.0
    side = position_side or ("LONG" if has_position else "FLAT")

    if side == "FLAT":
        if dev_pct <= -abs(vwap_dev_pct) and rsi_val <= rsi_oversold:
            return (
                "BUY",
                f"Oversold below VWAP (dev={dev_pct:.2f}%, RSI={rsi_val:.1f}, VWAP={vwap_val:.2f})",
                round(vwap_val, 2),
            )
        if allow_short and dev_pct >= abs(vwap_dev_pct) and rsi_val >= rsi_overbought:
            return (
                "SHORT",
                f"Overbought above VWAP (dev={dev_pct:+.2f}%, RSI={rsi_val:.1f}, VWAP={vwap_val:.2f})",
                round(vwap_val, 2),
            )
    elif side == "LONG":
        if last_close >= vwap_val or rsi_val >= rsi_exit:
            return (
                "EXIT",
                f"Mean reversion target hit (close={last_close:.2f}, VWAP={vwap_val:.2f}, RSI={rsi_val:.1f})",
                None,
            )
    elif side == "SHORT":
        if last_close <= vwap_val or rsi_val <= rsi_cover:
            return (
                "COVER",
                f"Short mean reversion target hit (close={last_close:.2f}, VWAP={vwap_val:.2f}, RSI={rsi_val:.1f})",
                None,
            )

    return (
        "HOLD",
        f"Close={last_close:.2f} VWAP={vwap_val:.2f} ({dev_pct:+.2f}%) RSI({rsi_period})={rsi_val:.1f}",
        None,
    )


class VwapRsiMeanReversionStrategy(BaseStrategy):
    """Fades intraday oversold extensions below VWAP with server-side bracket stops."""

    strategy_type = "vwap_rsi_reversion"
    description = (
        "Intraday Mean Reversion: buys dips stretched below VWAP with oversold RSI and attaches a server-side stop-loss."
    )

    def __init__(
        self,
        bot_id: str = "spy-vwap-reversion",
        symbol: str = "SPY",
        timeframe: Timeframe = "5 mins",
        stop_loss_pct: float | None = 1.0,
        take_profit_pct: float | None = 1.2,
        params: dict[str, Any] | None = None,
    ) -> None:
        default_params: dict[str, Any] = {
            "rsi_period": 14,
            "rsi_oversold": 30,
            "rsi_exit": 55,
            "vwap_dev_pct": 0.45,
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
        bars = await broker.get_historical_bars(
            self.symbol, count=60, timeframe=self.timeframe
        )
        positions = await broker.get_positions()
        cur_qty = next((p.quantity for p in positions if p.symbol == self.symbol), 0.0)
        pos_side = "LONG" if cur_qty > 0 else ("SHORT" if cur_qty < 0 else "FLAT")

        action, detail, vwap_target = vwap_rsi_signal(
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
            tp_override = (
                vwap_target
                if (
                    vwap_target
                    and ((action == "BUY" and vwap_target > last_price) or (action == "SHORT" and vwap_target < last_price))
                )
                else None
            )
            order = self.build_entry_order(
                side="BUY" if action == "BUY" else "SELL",
                quantity=qty,
                ref_price=last_price,
                take_profit_override=tp_override,
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

