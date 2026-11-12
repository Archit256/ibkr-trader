"""Multi-bot registry and lifecycle manager for automated trading strategies."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.broker.base import BrokerClient
from app.models import (
    STRATEGY_DEFAULT_TIMEFRAME,
    BotCreateRequest,
    StrategyInfo,
    StrategyType,
    Timeframe,
)
from app.strategies.base import ActivityLogger, BaseStrategy, NewsChecker, OrderSubmitter
from app.strategies.dual_momentum import DualMomentumRotationStrategy
from app.strategies.rsi2_pullback_swing import Rsi2PullbackSwingStrategy
from app.strategies.shock_reversion import ShockOverreactionStrategy
from app.strategies.sma_crossover import SmaCrossoverStrategy
from app.strategies.vwap_rsi_reversion import VwapRsiMeanReversionStrategy


def build_strategy(
    bot_id: str,
    strategy_type: StrategyType,
    symbol: str,
    timeframe: Timeframe | None = None,
    stop_loss_pct: float | None = 1.5,
    take_profit_pct: float | None = 2.5,
    params: dict[str, Any] | None = None,
) -> BaseStrategy:
    resolved_tf = timeframe or STRATEGY_DEFAULT_TIMEFRAME[strategy_type]
    if strategy_type == "vwap_rsi_reversion":
        return VwapRsiMeanReversionStrategy(
            bot_id=bot_id,
            symbol=symbol,
            timeframe=resolved_tf,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            params=params,
        )
    if strategy_type == "rsi2_pullback_swing":
        return Rsi2PullbackSwingStrategy(
            bot_id=bot_id,
            symbol=symbol,
            timeframe=resolved_tf,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            params=params,
        )
    if strategy_type == "shock_reversion":
        return ShockOverreactionStrategy(
            bot_id=bot_id,
            symbol=symbol,
            timeframe=resolved_tf,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            params=params,
        )
    if strategy_type == "dual_momentum":
        return DualMomentumRotationStrategy(
            bot_id=bot_id,
            symbol=symbol,
            timeframe=resolved_tf,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            params=params,
        )
    return SmaCrossoverStrategy(
        bot_id=bot_id,
        symbol=symbol,
        timeframe=resolved_tf,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        params=params,
    )


class StrategyManager:
    """Manages multiple bot instances and executes periodic evaluation ticks."""

    def __init__(self) -> None:
        self._strategies: dict[str, BaseStrategy] = {
            "spy-vwap-reversion": VwapRsiMeanReversionStrategy(
                bot_id="spy-vwap-reversion", symbol="SPY", timeframe="5 mins"
            ),
            "qqq-rsi2-swing": Rsi2PullbackSwingStrategy(
                bot_id="qqq-rsi2-swing", symbol="QQQ", timeframe="1 day"
            ),
            "nvda-shock-reversion": ShockOverreactionStrategy(
                bot_id="nvda-shock-reversion", symbol="NVDA", timeframe="5 mins"
            ),
            "sma_crossover": SmaCrossoverStrategy(
                bot_id="sma_crossover", symbol="NVDA", timeframe="5 mins"
            ),
            "universe-dual-momentum": DualMomentumRotationStrategy(
                bot_id="universe-dual-momentum", symbol="SPY", timeframe="1 day"
            ),
        }

    def list_strategies(self) -> list[StrategyInfo]:
        return [s.info() for s in self._strategies.values()]

    def load_or_create(
        self,
        bot_id: str,
        strategy_type: StrategyType,
        symbol: str,
        timeframe: Timeframe,
        stop_loss_pct: float | None,
        take_profit_pct: float | None,
        params: dict[str, Any] | None = None,
        trades_placed: int = 0,
        last_signal: str | None = None,
        last_checked_at: datetime | None = None,
    ) -> StrategyInfo:
        bot = build_strategy(
            bot_id=bot_id,
            strategy_type=strategy_type,
            symbol=symbol,
            timeframe=timeframe,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            params=params,
        )
        bot.trades_placed = trades_placed
        bot.last_signal = last_signal
        bot.last_checked_at = last_checked_at
        self._strategies[bot_id] = bot
        return bot.info()

    def create_bot(self, req: BotCreateRequest) -> StrategyInfo:
        if req.bot_id in self._strategies:
            raise ValueError(f"Bot '{req.bot_id}' already exists.")
        params = dict(req.params)
        if req.max_allocation_pct is not None:
            params["max_allocation_pct"] = req.max_allocation_pct
        if req.max_capital is not None:
            params["max_capital"] = req.max_capital
        return self.load_or_create(
            bot_id=req.bot_id,
            strategy_type=req.strategy_type,
            symbol=req.symbol,
            timeframe=req.timeframe,
            stop_loss_pct=req.stop_loss_pct,
            take_profit_pct=req.take_profit_pct,
            params=params,
        )

    def update_budget(
        self,
        bot_id: str,
        quantity: int | None = None,
        max_allocation_pct: float | None = None,
        max_capital: float | None = None,
        params: dict[str, Any] | None = None,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        update_sl: bool = False,
        update_tp: bool = False,
    ) -> StrategyInfo:
        """Update per-bot sizing, capital caps, SL/TP, and strategy parameters."""
        strategy = self._strategies.get(bot_id)
        if strategy is None:
            raise KeyError(f"Unknown bot '{bot_id}'")
        if update_sl:
            strategy.stop_loss_pct = stop_loss_pct
        if update_tp:
            strategy.take_profit_pct = take_profit_pct
        if params:
            strategy.params.update(params)
            if hasattr(strategy, "reset_rebalance_state"):
                strategy.reset_rebalance_state()  # type: ignore[attr-defined]
        if quantity is not None:
            strategy.params["quantity"] = quantity
        if max_allocation_pct is None:
            strategy.params.pop("max_allocation_pct", None)
        else:
            strategy.params["max_allocation_pct"] = float(max_allocation_pct)
        if max_capital is None:
            strategy.params.pop("max_capital", None)
        else:
            strategy.params["max_capital"] = float(max_capital)
        return strategy.info()

    def delete_bot(self, bot_id: str) -> None:
        if bot_id not in self._strategies:
            raise KeyError(f"Unknown bot '{bot_id}'")
        del self._strategies[bot_id]

    def configure(
        self,
        name: str,
        symbol: str | None = None,
        timeframe: Timeframe | None = None,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        params: dict[str, Any] | None = None,
        trades_placed: int | None = None,
        last_signal: str | None = None,
        last_checked_at: datetime | None = None,
    ) -> None:
        strategy = self._strategies.get(name)
        if strategy is None:
            return
        strategy.configure(
            symbol=symbol,
            timeframe=timeframe,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            params=params,
        )
        if trades_placed is not None:
            strategy.trades_placed = trades_placed
        if last_signal is not None:
            strategy.last_signal = last_signal
        if last_checked_at is not None:
            strategy.last_checked_at = last_checked_at

    def start(
        self,
        name: str,
        symbol: str | None = None,
        timeframe: Timeframe | None = None,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> StrategyInfo:
        strategy = self._strategies.get(name)
        if strategy is None:
            raise KeyError(f"Unknown strategy '{name}'")
        strategy.configure(
            symbol=symbol,
            timeframe=timeframe,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            params=params,
        )
        strategy.running = True
        return strategy.info()

    def stop(self, name: str) -> StrategyInfo:
        strategy = self._strategies.get(name)
        if strategy is None:
            raise KeyError(f"Unknown strategy '{name}'")
        strategy.running = False
        if hasattr(strategy, "reset_rebalance_state"):
            strategy.reset_rebalance_state()  # type: ignore[attr-defined]
        return strategy.info()

    def stop_all(self) -> None:
        for strategy in self._strategies.values():
            strategy.running = False
            if hasattr(strategy, "reset_rebalance_state"):
                strategy.reset_rebalance_state()  # type: ignore[attr-defined]

    async def tick_all(
        self,
        broker: BrokerClient,
        submit_order: OrderSubmitter,
        log_activity: ActivityLogger,
        news_checker: NewsChecker | None = None,
    ) -> None:
        for strategy in list(self._strategies.values()):
            if not strategy.running:
                continue
            try:
                await strategy.tick(
                    broker, submit_order, log_activity, news_checker=news_checker
                )
            except Exception as exc:
                strategy.last_signal = f"Error: {exc}"
                log_activity("error", "strategy", f"[{strategy.name}] {exc}")
