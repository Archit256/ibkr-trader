"""Cross-sectional Dual-Momentum Rotation strategy (relative + absolute momentum + 200-SMA filter)."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from app.broker.base import BrokerClient
from app.config import ETF_UNIVERSE, STARTER_UNIVERSE
from app.models import OrderRequest, PriceBar, Timeframe
from app.strategies.base import ActivityLogger, BaseStrategy, NewsChecker, OrderSubmitter
from app.strategies.indicators import sma


def resolve_momentum_horizons(
    momentum_mode: str = "single", lookback_days: int = 126
) -> tuple[int, ...]:
    """Return lookback windows (in trading days) for single or multi-horizon momentum blends."""
    if momentum_mode == "blend_3_6_12":
        return (63, 126, 252)
    if momentum_mode == "blend_1_3_6_12":
        return (21, 63, 126, 252)
    return (max(20, int(lookback_days)),)


def _annualized_vol(closes: list[float], window: int = 63) -> float:
    """Annualized daily return volatility over `window` days (floored at 5%)."""
    if len(closes) < 10:
        return 0.20
    tail = closes[-(window + 1) :]
    rets = [
        (tail[i] / tail[i - 1]) - 1.0
        for i in range(1, len(tail))
        if tail[i - 1] > 0
    ]
    if len(rets) < 5:
        return 0.20
    mean_r = sum(rets) / len(rets)
    var = sum((r - mean_r) ** 2 for r in rets) / len(rets)
    return max(0.05, math.sqrt(var * 252.0))


def rank_dual_momentum_universe(
    bars_by_symbol: dict[str, list[PriceBar]],
    index_by_symbol: dict[str, int],
    top_n: int = 3,
    lookback_days: int = 126,
    trend_sma: int = 200,
    defensive_symbol: str | None = "TLT",
    momentum_mode: str = "single",
    volatility_adjusted: bool = False,
) -> tuple[dict[str, float], str, list[dict[str, Any]]]:
    """Rank universe and return (target_weights, summary_detail, per_symbol_scores)."""
    offensive_scores: list[dict[str, Any]] = []
    defensive_row: dict[str, Any] | None = None
    horizons = resolve_momentum_horizons(momentum_mode, lookback_days)
    max_lb = max(horizons)

    for sym, bars in bars_by_symbol.items():
        idx = index_by_symbol.get(sym)
        if idx is None:
            continue
        needed = max(max_lb + 1, trend_sma)
        if idx + 1 < needed:
            continue

        window_closes = [b.close for b in bars[: idx + 1]]
        cur_close = window_closes[-1]
        horizon_rets: list[float] = []
        valid_horizons = True
        for h in horizons:
            past_close = window_closes[-1 - h]
            if past_close <= 0:
                valid_horizons = False
                break
            horizon_rets.append((cur_close / past_close) - 1.0)
        if not valid_horizons:
            continue

        trailing_ret = sum(horizon_rets) / float(len(horizon_rets))
        trend_val = sma(window_closes, trend_sma)
        if trend_val is None:
            continue

        ann_vol = _annualized_vol(window_closes)
        rank_score = (trailing_ret / ann_vol) if volatility_adjusted else trailing_ret
        passes = trailing_ret > 0.0 and cur_close > trend_val
        row = {
            "symbol": sym,
            "trailing_return_pct": round(trailing_ret * 100.0, 2),
            "last_close": round(cur_close, 2),
            "trend_sma": round(trend_val, 2),
            "ann_vol": ann_vol,
            "rank_score": rank_score,
            "passes_filter": passes,
            "is_defensive": bool(defensive_symbol and sym == defensive_symbol),
            "target_weight_pct": 0.0,
        }
        if row["is_defensive"]:
            defensive_row = row
        else:
            offensive_scores.append(row)

    offensive_scores.sort(key=lambda r: r["rank_score"], reverse=True)
    eligible = [r for r in offensive_scores if r["passes_filter"]]
    selected = eligible[:top_n]

    slot_weight = 1.0 / float(top_n)
    targets: dict[str, float] = {}
    if selected:
        offensive_budget = len(selected) * slot_weight
        if volatility_adjusted:
            inv_vols = [1.0 / r["ann_vol"] for r in selected]
            inv_sum = sum(inv_vols) or 1.0
            for r, iv in zip(selected, inv_vols):
                sym = r["symbol"]
                w = offensive_budget * (iv / inv_sum)
                targets[sym] = targets.get(sym, 0.0) + w
                r["target_weight_pct"] = round(targets[sym] * 100.0, 1)
        else:
            for r in selected:
                sym = r["symbol"]
                targets[sym] = targets.get(sym, 0.0) + slot_weight
                r["target_weight_pct"] = round(targets[sym] * 100.0, 1)

    unfilled_slots = top_n - len(selected)
    if unfilled_slots > 0 and defensive_row is not None and defensive_row["passes_filter"]:
        def_sym = defensive_row["symbol"]
        w = unfilled_slots * slot_weight
        targets[def_sym] = targets.get(def_sym, 0.0) + w
        defensive_row["target_weight_pct"] = round(targets[def_sym] * 100.0, 1)

    all_rows = list(offensive_scores)
    if defensive_row is not None:
        all_rows.append(defensive_row)

    if not offensive_scores and defensive_row is None:
        return {}, f"Waiting for bars (need {max(max_lb + 1, trend_sma)} daily bars per symbol)", all_rows

    if not targets:
        return {}, "100% Cash (no assets pass momentum + trend filter)", all_rows

    parts = [f"{sym} ({w * 100:.0f}%)" for sym, w in targets.items()]
    cash_pct = max(0.0, 1.0 - sum(targets.values())) * 100.0
    if cash_pct >= 1.0:
        parts.append(f"Cash ({cash_pct:.0f}%)")
    return targets, "Targets: " + ", ".join(parts), all_rows


def compute_dual_momentum_targets(
    bars_by_symbol: dict[str, list[PriceBar]],
    index_by_symbol: dict[str, int],
    top_n: int = 3,
    lookback_days: int = 126,
    trend_sma: int = 200,
    defensive_symbol: str | None = "TLT",
    momentum_mode: str = "single",
    volatility_adjusted: bool = False,
) -> tuple[dict[str, float], str]:
    """Rank universe by trailing return (single or blended); require positive return and price > trend SMA."""
    targets, detail, _ = rank_dual_momentum_universe(
        bars_by_symbol=bars_by_symbol,
        index_by_symbol=index_by_symbol,
        top_n=top_n,
        lookback_days=lookback_days,
        trend_sma=trend_sma,
        defensive_symbol=defensive_symbol,
        momentum_mode=momentum_mode,
        volatility_adjusted=volatility_adjusted,
    )
    return targets, detail


class DualMomentumRotationStrategy(BaseStrategy):
    """Monthly cross-sectional dual-momentum rotation across the starter universe."""

    strategy_type = "dual_momentum"
    description = (
        "Monthly Dual-Momentum Rotation: ranks universe by 6M return, holds top 3 above 200-SMA, and shifts weak slots to TLT or cash."
    )

    def __init__(
        self,
        bot_id: str = "universe-dual-momentum",
        symbol: str = "SPY",
        timeframe: Timeframe = "1 day",
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        default_params: dict[str, Any] = {
            "top_n": 3,
            "lookback_days": 126,
            "momentum_mode": "single",
            "etfs_only": False,
            "volatility_adjusted": False,
            "trend_sma": 200,
            "defensive_symbol": "TLT",
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
        self._last_rebalance_month: str | None = None
        self._last_rebalance_summary: str = ""

    def reset_rebalance_state(self) -> None:
        self._last_rebalance_month = None
        self._last_rebalance_summary = ""

    async def evaluate(
        self,
        broker: BrokerClient,
        submit_order: OrderSubmitter,
        log_activity: ActivityLogger,
        news_checker: NewsChecker | None = None,
    ) -> str:
        cur_month = datetime.now(timezone.utc).strftime("%Y-%m")
        if self._last_rebalance_month == cur_month:
            summary = self._last_rebalance_summary or "No positions"
            return f"{cur_month} active ({summary}) — next rebalance next month"

        top_n = max(1, int(self.params.get("top_n", 3)))
        lookback_days = max(20, int(self.params.get("lookback_days", 126)))
        momentum_mode = str(self.params.get("momentum_mode", "single"))
        etfs_only = bool(self.params.get("etfs_only", False))
        volatility_adjusted = bool(self.params.get("volatility_adjusted", False))
        trend_sma = max(20, int(self.params.get("trend_sma", 200)))
        defensive = str(self.params.get("defensive_symbol") or "").strip().upper() or None
        max_lb = max(resolve_momentum_horizons(momentum_mode, lookback_days))
        needed = max(max_lb + 2, trend_sma + 5)

        universe = list(ETF_UNIVERSE if etfs_only else STARTER_UNIVERSE)
        if defensive and defensive not in universe:
            universe.append(defensive)

        bars_by_symbol: dict[str, list[PriceBar]] = {}
        index_by_symbol: dict[str, int] = {}
        for sym in universe:
            bars = await broker.get_historical_bars(sym, count=needed, timeframe="1 day")
            if bars:
                bars_by_symbol[sym] = bars
                index_by_symbol[sym] = len(bars) - 1

        targets, detail = compute_dual_momentum_targets(
            bars_by_symbol=bars_by_symbol,
            index_by_symbol=index_by_symbol,
            top_n=top_n,
            lookback_days=lookback_days,
            trend_sma=trend_sma,
            defensive_symbol=defensive,
            momentum_mode=momentum_mode,
            volatility_adjusted=volatility_adjusted,
        )

        account = await broker.get_account_summary()
        budget_cap = await self.resolve_capital_budget(broker)
        capital_pool = (
            min(account.net_liquidation, budget_cap)
            if budget_cap is not None
            else account.net_liquidation
        )
        positions = await broker.get_positions()
        pos_qty = {p.symbol: int(p.quantity) for p in positions if p.quantity > 0}
        universe_set = set(universe)

        log_activity("info", "strategy", f"[{self.name}] Selected for {cur_month}: {detail}")

        try:
            # Sell positions no longer in target or overweight
            for sym, cur_shares in pos_qty.items():
                if sym not in universe_set:
                    continue
                target_w = targets.get(sym, 0.0)
                last_p = bars_by_symbol[sym][-1].close if sym in bars_by_symbol else 0.0
                target_shares = int((capital_pool * target_w) // last_p) if last_p > 0 else 0
                if cur_shares > target_shares:
                    sell_qty = cur_shares - target_shares
                    await submit_order(
                        self.build_order(
                            side="SELL",
                            quantity=sell_qty,
                            ref_price=last_p,
                            symbol=sym,
                        )
                    )
                    self.trades_placed += 1

            # Buy new or underweight target positions
            for sym, target_w in targets.items():
                last_p = bars_by_symbol[sym][-1].close if sym in bars_by_symbol else 0.0
                if last_p <= 0:
                    continue
                target_shares = int((capital_pool * target_w) // last_p)
                cur_shares = pos_qty.get(sym, 0)
                if target_shares > cur_shares:
                    buy_qty = target_shares - cur_shares
                    await submit_order(
                        self.build_order(
                            side="BUY",
                            quantity=buy_qty,
                            ref_price=last_p,
                            symbol=sym,
                        )
                    )
                    self.trades_placed += 1
        except Exception as exc:
            raise RuntimeError(f"{detail} | Order blocked: {exc}") from exc

        self._last_rebalance_month = cur_month
        self._last_rebalance_summary = detail
        log_activity("info", "strategy", f"[{self.name}] Monthly rebalance ({cur_month}): {detail}")
        return f"Rebalanced {cur_month} — {detail}"

