"""Volume-Shock Overreaction Exhaustion strategy combined with NewsGuard veto."""

from __future__ import annotations

from typing import Any

from app.broker.base import BrokerClient
from app.models import OrderRequest, PriceBar, Timeframe
from app.strategies.base import ActivityLogger, BaseStrategy, NewsChecker, OrderSubmitter
from app.strategies.indicators import atr, ema, rolling_vwap


def shock_reversion_signal(
    bars: list[PriceBar],
    has_position: bool,
    params: dict[str, Any],
) -> tuple[str, str, float | None, float | None]:
    """Detect sudden volume-backed panic drops and enter only after exhaustion confirmation.

    Returns (action, detail, suggested_sl, suggested_tp).
    """
    atr_period = max(5, int(params.get("atr_period", 14)))
    shock_atr_mult = float(params.get("shock_atr_mult", 1.8))
    vol_mult = float(params.get("vol_mult", 2.0))
    lookback = max(2, int(params.get("shock_lookback", 4)))
    bounce_atr_mult = float(params.get("bounce_atr_mult", 1.5))

    needed = max(atr_period + 3, 22, lookback + 2)
    if len(bars) < needed:
        return "HOLD", f"Waiting for bars ({len(bars)}/{needed})", None, None

    cur_atr = atr(bars[:-1], period=atr_period)
    if cur_atr is None or cur_atr <= 0:
        return "HOLD", "Insufficient ATR history", None, None

    closes = [b.close for b in bars]
    ema9 = ema(closes, 9)
    vwap_val = rolling_vwap(bars)
    latest = bars[-1]

    if has_position:
        if (vwap_val and latest.close >= vwap_val) or (ema9 and latest.close >= ema9 * 1.003):
            return (
                "EXIT",
                f"Shock bounce exit (Close={latest.close:.2f}, EMA9={ema9 or 0:.2f}, VWAP={vwap_val or 0:.2f})",
                None,
                None,
            )
        return (
            "HOLD",
            f"Holding bounce (Close={latest.close:.2f}, VWAP={vwap_val or 0:.2f})",
            None,
            None,
        )

    # Scan recent prior bars (not latest bar itself) for a high-volume panic drop
    avg_vol = sum(b.volume for b in bars[-21:-1]) / 20.0
    if avg_vol <= 0:
        return "HOLD", "Zero baseline volume", None, None

    recent_window = bars[-(lookback + 1) : -1]
    shock_bar: PriceBar | None = None
    shock_pre_close: float = 0.0
    for idx, b in enumerate(recent_window):
        prev_bar = bars[len(bars) - (lookback + 1) + idx - 1]
        drop = prev_bar.close - b.close
        if drop >= shock_atr_mult * cur_atr and b.volume >= vol_mult * avg_vol:
            shock_bar = b
            shock_pre_close = prev_bar.close

    if shock_bar is None:
        return (
            "HOLD",
            f"No shock (ATR={cur_atr:.2f}, Vol={latest.volume:,}, AvgVol={int(avg_vol):,})",
            None,
            None,
        )

    # Require exhaustion confirmation: green reversal candle holding above shock low
    # while still meaningfully discounted below the pre-shock level
    is_green_reversal = latest.close > latest.open
    holds_low = latest.low >= shock_bar.low * 0.997
    still_discounted = latest.close < shock_pre_close * 0.995
    if is_green_reversal and holds_low and still_discounted:
        suggested_sl = round(max(0.01, shock_bar.low * 0.997), 2)
        suggested_tp = round(latest.close + bounce_atr_mult * cur_atr, 2)
        if vwap_val and vwap_val > latest.close:
            suggested_tp = round(min(suggested_tp, vwap_val), 2)
        if suggested_tp <= latest.close:
            suggested_tp = round(latest.close + bounce_atr_mult * cur_atr, 2)
        return (
            "BUY",
            f"Shock exhaustion (ShockLow={shock_bar.low:.2f}, Vol={shock_bar.volume:,}, ATR={cur_atr:.2f})",
            suggested_sl,
            suggested_tp,
        )

    return (
        "HOLD",
        f"Shock detected ({shock_bar.time}); waiting for green stabilization bar",
        None,
        None,
    )


class ShockOverreactionStrategy(BaseStrategy):
    """Fades sudden non-fundamental panic drops after volume exhaustion, vetoed by NewsGuard."""

    strategy_type = "shock_reversion"
    description = (
        "Overreaction Exhaustion: detects sudden ATR + volume shocks, checks NewsGuard for fundamental catalysts, and enters on stabilization with a server-side stop below shock low."
    )

    def __init__(
        self,
        bot_id: str = "nvda-shock-reversion",
        symbol: str = "NVDA",
        timeframe: Timeframe = "5 mins",
        stop_loss_pct: float | None = 1.5,
        take_profit_pct: float | None = 2.0,
        params: dict[str, Any] | None = None,
    ) -> None:
        default_params: dict[str, Any] = {
            "atr_period": 14,
            "shock_atr_mult": 1.8,
            "vol_mult": 2.0,
            "shock_lookback": 4,
            "bounce_atr_mult": 1.5,
            "quantity": 10,
            "respect_news_guard": True,
            "cooldown_seconds": 300,
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

        action, detail, sl_override, tp_override = shock_reversion_signal(
            bars=bars, has_position=cur_qty > 0, params=self.params
        )

        if action == "BUY" and cur_qty <= 0:
            if self.is_in_cooldown():
                return f"Cooldown active ({self.cooldown_seconds}s) — skipped {detail}"

            veto = await self.check_news_veto(news_checker)
            if veto:
                msg = f"NewsGuard vetoed BUY {self.symbol}: {veto}"
                log_activity("warning", "news", f"[{self.name}] {msg}")
                return msg

            last_price = bars[-1].close
            qty = await self.resolve_entry_quantity(broker, last_price, qty)
            if qty <= 0:
                return f"Skipped BUY {self.symbol}: price ${last_price:.2f} exceeds bot budget"
            order = self.build_entry_order(
                side="BUY",
                quantity=qty,
                ref_price=last_price,
                stop_loss_override=sl_override,
                take_profit_override=tp_override,
            )
            await submit_order(order)
            self.mark_entry_placed()
            self.trades_placed += 1
            log_activity("success", "strategy", f"[{self.name}] BUY {qty} {self.symbol}: {detail}")
            return f"BUY {qty} {self.symbol} — {detail}"

        if action == "EXIT" and cur_qty > 0:
            sell_qty = int(abs(cur_qty))
            await submit_order(self.build_order(side="SELL", quantity=sell_qty, ref_price=bars[-1].close))
            self.trades_placed += 1
            log_activity("warning", "strategy", f"[{self.name}] EXIT {sell_qty} {self.symbol}: {detail}")
            return f"SELL {sell_qty} {self.symbol} — {detail}"

        return detail
