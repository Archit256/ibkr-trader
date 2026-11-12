"""Historical backtesting engine for single-bot and multi-bot portfolio runs."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.config import ETF_UNIVERSE
from app.models import (
    BacktestRequest,
    BacktestResult,
    BacktestTrade,
    BenchmarkMetrics,
    EquityPoint,
    PortfolioBacktestRequest,
    PortfolioBacktestResult,
    PortfolioBacktestTrade,
    PortfolioBotConfig,
    PortfolioBotResult,
    PriceBar,
    RotationBacktestRequest,
    RotationWalkForwardRequest,
    RotationWalkForwardResult,
    StrategyType,
    Timeframe,
    WalkForwardRequest,
    WalkForwardResult,
    WalkForwardVerdict,
)
from app.strategies.dual_momentum import (
    compute_dual_momentum_targets,
    resolve_momentum_horizons,
)
from app.strategies.rsi2_pullback_swing import rsi2_swing_signal
from app.strategies.shock_reversion import shock_reversion_signal
from app.strategies.sma_crossover import sma_crossover_signal
from app.strategies.vwap_rsi_reversion import vwap_rsi_signal


def _compute_curve_stats(
    values: list[float], initial_capital: float, timeframe: Timeframe
) -> tuple[float, float, float]:
    """Return (total_return_pct, max_drawdown_pct, sharpe_ratio) for an equity series."""
    final_val = values[-1] if values else initial_capital
    total_return_pct = ((final_val - initial_capital) / initial_capital) * 100.0

    peak = initial_capital
    max_dd = 0.0
    for val in values:
        if val > peak:
            peak = val
        if peak > 0:
            dd = ((peak - val) / peak) * 100.0
            if dd > max_dd:
                max_dd = dd

    rets: list[float] = []
    for i in range(1, len(values)):
        prev_val = values[i - 1]
        if prev_val > 0:
            rets.append((values[i] - prev_val) / prev_val)

    sharpe = 0.0
    if len(rets) > 2:
        mean_r = sum(rets) / len(rets)
        var_r = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
        std_r = math.sqrt(var_r)
        if std_r > 0:
            ann_factor = 252.0 if timeframe == "1 day" else 252.0 * 26.0
            sharpe = (mean_r / std_r) * math.sqrt(ann_factor)

    return total_return_pct, max_dd, sharpe


def _evaluate_signal(
    strategy_type: StrategyType,
    window: list[PriceBar],
    has_position: bool,
    params: dict[str, Any],
    prev_sma_regime: str | None,
    position_side: str | None = None,
) -> tuple[str, float | None, float | None, str | None]:
    """Dispatch strategy signal evaluation and return (action, sl_override, tp_override, next_sma_regime)."""
    side = position_side or ("LONG" if has_position else "FLAT")
    if strategy_type == "vwap_rsi_reversion":
        action, _, tp_override = vwap_rsi_signal(window, has_position, params, position_side=side)
        return action, None, tp_override, prev_sma_regime
    if strategy_type == "rsi2_pullback_swing":
        action, _ = rsi2_swing_signal(window, has_position, params, position_side=side)
        return action, None, None, prev_sma_regime
    if strategy_type == "shock_reversion":
        action, _, sl_override, tp_override = shock_reversion_signal(
            window, has_position, params
        )
        return action, sl_override, tp_override, prev_sma_regime

    regime, _ = sma_crossover_signal(window, params)
    action = "HOLD"
    next_regime = prev_sma_regime
    if regime != "WAIT":
        if prev_sma_regime is not None and regime != prev_sma_regime:
            if regime == "BULL" and not has_position:
                action = "BUY"
            elif regime == "BEAR" and has_position:
                action = "EXIT"
        next_regime = regime
    return action, None, None, next_regime


def _summarize_trades(trades: list[BacktestTrade] | list[PortfolioBacktestTrade]) -> tuple[float, float]:
    """Return (win_rate_pct, profit_factor) across completed trades."""
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    win_rate = (len(wins) / len(trades) * 100.0) if trades else 0.0
    gross_win = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = (
        (gross_win / gross_loss) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)
    )
    return win_rate, profit_factor


def _downsample_curve(equity_curve: list[EquityPoint], max_points: int = 300) -> list[EquityPoint]:
    if len(equity_curve) <= max_points:
        return equity_curve
    step = max(1, len(equity_curve) // max_points)
    sampled = equity_curve[::step]
    if sampled[-1].time != equity_curve[-1].time:
        sampled.append(equity_curve[-1])
    return sampled


def _resolve_brackets(
    fill_entry: float,
    stop_loss_pct: float | None,
    take_profit_pct: float | None,
    sl_override: float | None,
    tp_override: float | None,
    is_short: bool = False,
) -> tuple[float | None, float | None]:
    if is_short:
        sl_price: float | None = (
            sl_override
            if (sl_override and sl_override > fill_entry)
            else (round(fill_entry * (1.0 + stop_loss_pct / 100.0), 2) if stop_loss_pct else None)
        )
        if take_profit_pct:
            tp_price: float | None = round(max(0.01, fill_entry * (1.0 - take_profit_pct / 100.0)), 2)
        elif tp_override and tp_override < fill_entry:
            tp_price = tp_override
        else:
            tp_price = None
        return sl_price, tp_price

    if sl_override and sl_override < fill_entry:
        sl_price = sl_override
    else:
        sl_price = (
            round(fill_entry * (1.0 - stop_loss_pct / 100.0), 2)
            if stop_loss_pct
            else None
        )

    if take_profit_pct:
        tp_price = round(fill_entry * (1.0 + take_profit_pct / 100.0), 2)
    elif tp_override and tp_override > fill_entry:
        tp_price = tp_override
    else:
        tp_price = None
    return sl_price, tp_price


def _compute_entry_quantity(
    cash: float,
    total_equity: float,
    fill_entry: float,
    commission_per_share: float,
    fixed_quantity: int,
    allocation_pct: float | None,
) -> int:
    """Return shares to buy using fixed sizing or compounded % of equity."""
    per_share_cost = fill_entry + commission_per_share
    if per_share_cost <= 0:
        return 0
    if allocation_pct is None:
        return fixed_quantity if cash >= fixed_quantity * per_share_cost else 0
    target_budget = min(cash, total_equity * (allocation_pct / 100.0))
    return max(0, int(target_budget // per_share_cost))


def run_backtest(bars: list[PriceBar], req: BacktestRequest) -> BacktestResult:
    """Replay historical bars using the exact shared strategy signal evaluators."""
    if len(bars) < 20:
        raise ValueError(
            f"Need at least 20 cached bars for {req.symbol} ({req.timeframe}); found {len(bars)}. Sync data first."
        )

    assert req.timeframe is not None
    cash = float(req.initial_capital)
    qty = req.quantity
    slip_mult = req.slippage_bps / 10_000.0
    pos_qty = 0
    entry_price = 0.0
    entry_time = ""
    sl_price: float | None = None
    tp_price: float | None = None
    prev_sma_regime: str | None = None
    bars_in_market = 0

    bench_start = bars[0].close if bars[0].close > 0 else 1.0
    bench_values: list[float] = []

    trades: list[BacktestTrade] = []
    equity_curve: list[EquityPoint] = []

    def close_position(raw_exit: float, exit_time: str, reason: str) -> None:
        nonlocal cash, pos_qty, entry_price, entry_time, sl_price, tp_price
        abs_qty = abs(pos_qty)
        if pos_qty > 0:
            fill_exit = round(raw_exit * (1.0 - slip_mult), 4)
            comm = req.commission_per_share * abs_qty
            cash += abs_qty * fill_exit - comm
            net_pnl = (fill_exit - entry_price) * abs_qty - (2.0 * comm)
            ret_pct = ((fill_exit - entry_price) / entry_price * 100.0) if entry_price else 0.0
            trade_side: Literal["BUY", "SELL"] = "BUY"
        else:
            fill_exit = round(raw_exit * (1.0 + slip_mult), 4)
            comm = req.commission_per_share * abs_qty
            cash -= abs_qty * fill_exit + comm
            net_pnl = (entry_price - fill_exit) * abs_qty - (2.0 * comm)
            ret_pct = ((entry_price - fill_exit) / entry_price * 100.0) if entry_price else 0.0
            trade_side = "SELL"
        trades.append(
            BacktestTrade(
                entry_time=entry_time,
                exit_time=exit_time,
                side=trade_side,
                quantity=abs_qty,
                entry_price=round(entry_price, 2),
                exit_price=round(fill_exit, 2),
                pnl=round(net_pnl, 2),
                return_pct=round(ret_pct, 2),
                exit_reason=reason,
            )
        )
        pos_qty = 0
        entry_price = 0.0
        entry_time = ""
        sl_price = None
        tp_price = None

    for idx, bar in enumerate(bars):
        closed_this_bar = False
        if pos_qty > 0:
            if sl_price is not None and bar.low <= sl_price:
                close_position(sl_price, bar.time, "Stop-Loss")
                closed_this_bar = True
            elif tp_price is not None and bar.high >= tp_price:
                close_position(tp_price, bar.time, "Take-Profit")
                closed_this_bar = True
        elif pos_qty < 0:
            if sl_price is not None and bar.high >= sl_price:
                close_position(sl_price, bar.time, "Stop-Loss")
                closed_this_bar = True
            elif tp_price is not None and bar.low <= tp_price:
                close_position(tp_price, bar.time, "Take-Profit")
                closed_this_bar = True

        pos_side = "LONG" if pos_qty > 0 else ("SHORT" if pos_qty < 0 else "FLAT")
        action, sl_override, tp_override, prev_sma_regime = _evaluate_signal(
            req.strategy_type,
            bars[: idx + 1],
            pos_qty > 0,
            req.params,
            prev_sma_regime,
            position_side=pos_side,
        )

        if action == "BUY" and pos_qty == 0 and not closed_this_bar:
            fill_entry = round(bar.close * (1.0 + slip_mult), 4)
            buy_qty = _compute_entry_quantity(
                cash=cash,
                total_equity=cash,
                fill_entry=fill_entry,
                commission_per_share=req.commission_per_share,
                fixed_quantity=qty,
                allocation_pct=req.allocation_pct,
            )
            comm = req.commission_per_share * buy_qty
            if buy_qty > 0 and cash >= buy_qty * fill_entry + comm:
                cash -= buy_qty * fill_entry + comm
                pos_qty = buy_qty
                entry_price = fill_entry
                entry_time = bar.time
                sl_price, tp_price = _resolve_brackets(
                    fill_entry,
                    req.stop_loss_pct,
                    req.take_profit_pct,
                    sl_override,
                    tp_override,
                )
        elif action == "SHORT" and pos_qty == 0 and not closed_this_bar:
            fill_entry = round(bar.close * (1.0 - slip_mult), 4)
            short_qty = _compute_entry_quantity(
                cash=cash,
                total_equity=cash,
                fill_entry=fill_entry,
                commission_per_share=req.commission_per_share,
                fixed_quantity=qty,
                allocation_pct=req.allocation_pct,
            )
            comm = req.commission_per_share * short_qty
            if short_qty > 0 and cash >= short_qty * fill_entry + comm:
                cash += short_qty * fill_entry - comm
                pos_qty = -short_qty
                entry_price = fill_entry
                entry_time = bar.time
                sl_price, tp_price = _resolve_brackets(
                    fill_entry,
                    req.stop_loss_pct,
                    req.take_profit_pct,
                    sl_override,
                    tp_override,
                    is_short=True,
                )
        elif (action == "EXIT" and pos_qty > 0) or (action == "COVER" and pos_qty < 0):
            close_position(bar.close, bar.time, "Strategy Exit")

        if pos_qty != 0:
            bars_in_market += 1

        equity = cash + (pos_qty * bar.close)
        bench_eq = req.initial_capital * (bar.close / bench_start)
        bench_values.append(round(bench_eq, 2))
        equity_curve.append(
            EquityPoint(
                time=bar.time,
                equity=round(equity, 2),
                benchmark_equity=round(bench_eq, 2),
            )
        )

    if pos_qty != 0:
        close_position(bars[-1].close, bars[-1].time, "End of Backtest")
        equity_curve[-1] = EquityPoint(
            time=bars[-1].time,
            equity=round(cash, 2),
            benchmark_equity=bench_values[-1],
        )

    strategy_values = [pt.equity for pt in equity_curve]
    final_equity = strategy_values[-1] if strategy_values else req.initial_capital
    total_return_pct, max_dd, sharpe = _compute_curve_stats(
        strategy_values, req.initial_capital, req.timeframe
    )
    bench_final = bench_values[-1] if bench_values else req.initial_capital
    bench_return_pct, bench_max_dd, bench_sharpe = _compute_curve_stats(
        bench_values, req.initial_capital, req.timeframe
    )
    exposure_pct = (bars_in_market / len(bars) * 100.0) if bars else 0.0
    win_rate, profit_factor = _summarize_trades(trades)

    return BacktestResult(
        strategy_type=req.strategy_type,
        symbol=req.symbol,
        timeframe=req.timeframe,
        bars_tested=len(bars),
        initial_capital=round(req.initial_capital, 2),
        final_equity=round(final_equity, 2),
        total_return_pct=round(total_return_pct, 2),
        sharpe_ratio=round(sharpe, 2),
        max_drawdown_pct=round(max_dd, 2),
        win_rate_pct=round(win_rate, 1),
        profit_factor=round(profit_factor, 2),
        total_trades=len(trades),
        exposure_pct=round(exposure_pct, 1),
        alpha_vs_benchmark_pct=round(total_return_pct - bench_return_pct, 2),
        benchmark=BenchmarkMetrics(
            label=f"Buy & Hold ({req.symbol})",
            final_equity=round(bench_final, 2),
            total_return_pct=round(bench_return_pct, 2),
            sharpe_ratio=round(bench_sharpe, 2),
            max_drawdown_pct=round(bench_max_dd, 2),
        ),
        equity_curve=_downsample_curve(equity_curve),
        trades=trades,
    )


@dataclass
class _BotSimState:
    cfg: PortfolioBotConfig
    bars: list[PriceBar]
    index_by_time: dict[str, int]
    bench_allocation: float
    pos_qty: int = 0
    entry_price: float = 0.0
    entry_time: str = ""
    sl_price: float | None = None
    tp_price: float | None = None
    prev_sma_regime: str | None = None
    last_close: float = 0.0
    bench_shares: float | None = None
    bars_in_market: int = 0
    skipped_for_cash: int = 0
    trades: list[PortfolioBacktestTrade] = field(default_factory=list)


def run_portfolio_backtest(
    bars_by_bot: dict[str, list[PriceBar]],
    req: PortfolioBacktestRequest,
) -> PortfolioBacktestResult:
    """Replay multiple bots simultaneously against a shared cash account."""
    states: list[_BotSimState] = []
    alloc_per_bot = float(req.initial_capital) / len(req.bots)

    for bot in req.bots:
        assert bot.bot_id is not None
        assert bot.timeframe is not None
        bars = bars_by_bot.get(bot.bot_id, [])
        if len(bars) < 20:
            raise ValueError(
                f"Need at least 20 cached bars for {bot.symbol} ({bot.timeframe}); found {len(bars)}. Sync data first."
            )
        index_by_time = {bar.time: idx for idx, bar in enumerate(bars)}
        states.append(
            _BotSimState(
                cfg=bot,
                bars=bars,
                index_by_time=index_by_time,
                bench_allocation=alloc_per_bot,
            )
        )

    all_times = sorted({bar.time for s in states for bar in s.bars})
    cash = float(req.initial_capital)
    slip_mult = req.slippage_bps / 10_000.0
    all_trades: list[PortfolioBacktestTrade] = []
    equity_curve: list[EquityPoint] = []
    bench_values: list[float] = []
    max_concurrent = 0
    points_with_pos = 0

    def close_bot_position(state: _BotSimState, raw_exit: float, exit_time: str, reason: str) -> None:
        nonlocal cash
        assert state.cfg.bot_id is not None
        fill_exit = round(raw_exit * (1.0 - slip_mult), 4)
        comm = req.commission_per_share * state.pos_qty
        cash += state.pos_qty * fill_exit - comm
        net_pnl = (fill_exit - state.entry_price) * state.pos_qty - (2.0 * comm)
        ret_pct = (
            ((fill_exit - state.entry_price) / state.entry_price * 100.0)
            if state.entry_price
            else 0.0
        )
        trade = PortfolioBacktestTrade(
            bot_id=state.cfg.bot_id,
            symbol=state.cfg.symbol,
            entry_time=state.entry_time,
            exit_time=exit_time,
            side="BUY",
            quantity=state.pos_qty,
            entry_price=round(state.entry_price, 2),
            exit_price=round(fill_exit, 2),
            pnl=round(net_pnl, 2),
            return_pct=round(ret_pct, 2),
            exit_reason=reason,
        )
        state.trades.append(trade)
        all_trades.append(trade)
        state.pos_qty = 0
        state.entry_price = 0.0
        state.entry_time = ""
        state.sl_price = None
        state.tp_price = None

    for ts in all_times:
        for state in states:
            bar_idx = state.index_by_time.get(ts)
            if bar_idx is None:
                continue

            bar = state.bars[bar_idx]
            state.last_close = bar.close
            if state.bench_shares is None and bar.close > 0:
                state.bench_shares = state.bench_allocation / bar.close

            closed_this_bar = False
            if state.pos_qty > 0:
                if state.sl_price is not None and bar.low <= state.sl_price:
                    close_bot_position(state, state.sl_price, bar.time, "Stop-Loss")
                    closed_this_bar = True
                elif state.tp_price is not None and bar.high >= state.tp_price:
                    close_bot_position(state, state.tp_price, bar.time, "Take-Profit")
                    closed_this_bar = True

            action, sl_override, tp_override, state.prev_sma_regime = _evaluate_signal(
                state.cfg.strategy_type,
                state.bars[: bar_idx + 1],
                state.pos_qty > 0,
                state.cfg.params,
                state.prev_sma_regime,
            )

            if action == "BUY" and state.pos_qty == 0 and not closed_this_bar:
                fill_entry = round(bar.close * (1.0 + slip_mult), 4)
                portfolio_equity = cash + sum(s.pos_qty * s.last_close for s in states)
                buy_qty = _compute_entry_quantity(
                    cash=cash,
                    total_equity=portfolio_equity,
                    fill_entry=fill_entry,
                    commission_per_share=req.commission_per_share,
                    fixed_quantity=state.cfg.quantity,
                    allocation_pct=state.cfg.allocation_pct,
                )
                comm = req.commission_per_share * buy_qty
                if buy_qty > 0 and cash >= buy_qty * fill_entry + comm:
                    cash -= buy_qty * fill_entry + comm
                    state.pos_qty = buy_qty
                    state.entry_price = fill_entry
                    state.entry_time = bar.time
                    state.sl_price, state.tp_price = _resolve_brackets(
                        fill_entry,
                        state.cfg.stop_loss_pct,
                        state.cfg.take_profit_pct,
                        sl_override,
                        tp_override,
                    )
                else:
                    state.skipped_for_cash += 1
            elif action == "EXIT" and state.pos_qty > 0:
                close_bot_position(state, bar.close, bar.time, "Strategy Exit")

            if state.pos_qty > 0:
                state.bars_in_market += 1

        open_now = sum(1 for s in states if s.pos_qty > 0)
        if open_now > 0:
            points_with_pos += 1
            if open_now > max_concurrent:
                max_concurrent = open_now

        equity = cash + sum(s.pos_qty * s.last_close for s in states)
        bench_eq = sum(
            (s.bench_shares * s.last_close) if s.bench_shares is not None else s.bench_allocation
            for s in states
        )
        bench_values.append(round(bench_eq, 2))
        equity_curve.append(
            EquityPoint(
                time=ts,
                equity=round(equity, 2),
                benchmark_equity=round(bench_eq, 2),
            )
        )

    for state in states:
        if state.pos_qty > 0:
            last_bar = state.bars[-1]
            close_bot_position(state, last_bar.close, last_bar.time, "End of Backtest")
    if equity_curve:
        equity_curve[-1] = EquityPoint(
            time=all_times[-1],
            equity=round(cash, 2),
            benchmark_equity=bench_values[-1],
        )

    ann_timeframe: Timeframe = (
        "1 day" if all(s.cfg.timeframe == "1 day" for s in states) else "5 mins"
    )
    strategy_values = [pt.equity for pt in equity_curve]
    final_equity = strategy_values[-1] if strategy_values else req.initial_capital
    total_return_pct, max_dd, sharpe = _compute_curve_stats(
        strategy_values, req.initial_capital, ann_timeframe
    )
    bench_final = bench_values[-1] if bench_values else req.initial_capital
    bench_return_pct, bench_max_dd, bench_sharpe = _compute_curve_stats(
        bench_values, req.initial_capital, ann_timeframe
    )
    exposure_pct = (points_with_pos / len(all_times) * 100.0) if all_times else 0.0
    win_rate, profit_factor = _summarize_trades(all_trades)

    bot_results: list[PortfolioBotResult] = []
    for state in states:
        assert state.cfg.bot_id is not None
        assert state.cfg.timeframe is not None
        bot_win_rate, _ = _summarize_trades(state.trades)
        bot_net_pnl = sum(t.pnl for t in state.trades)
        bot_exp = (state.bars_in_market / len(state.bars) * 100.0) if state.bars else 0.0
        bot_results.append(
            PortfolioBotResult(
                bot_id=state.cfg.bot_id,
                symbol=state.cfg.symbol,
                strategy_type=state.cfg.strategy_type,
                timeframe=state.cfg.timeframe,
                bars_tested=len(state.bars),
                total_trades=len(state.trades),
                win_rate_pct=round(bot_win_rate, 1),
                net_pnl=round(bot_net_pnl, 2),
                exposure_pct=round(bot_exp, 1),
                skipped_for_cash=state.skipped_for_cash,
            )
        )

    bench_label = (
        f"Buy & Hold ({states[0].cfg.symbol})"
        if len(states) == 1
        else f"Equal-Weight Buy & Hold ({len(states)} symbols)"
    )

    return PortfolioBacktestResult(
        initial_capital=round(req.initial_capital, 2),
        final_equity=round(final_equity, 2),
        total_return_pct=round(total_return_pct, 2),
        sharpe_ratio=round(sharpe, 2),
        max_drawdown_pct=round(max_dd, 2),
        win_rate_pct=round(win_rate, 1),
        profit_factor=round(profit_factor, 2),
        total_trades=len(all_trades),
        exposure_pct=round(exposure_pct, 1),
        max_concurrent_positions=max_concurrent,
        skipped_for_cash=sum(s.skipped_for_cash for s in states),
        alpha_vs_benchmark_pct=round(total_return_pct - bench_return_pct, 2),
        benchmark=BenchmarkMetrics(
            label=bench_label,
            final_equity=round(bench_final, 2),
            total_return_pct=round(bench_return_pct, 2),
            sharpe_ratio=round(bench_sharpe, 2),
            max_drawdown_pct=round(bench_max_dd, 2),
        ),
        bot_results=bot_results,
        equity_curve=_downsample_curve(equity_curve),
        trades=all_trades,
    )


def _candidate_param_grid(
    strategy_type: StrategyType, is_bar_count: int, base_params: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return parameter combinations to evaluate on the in-sample window."""
    if strategy_type == "rsi2_pullback_swing":
        combos = [
            {**base_params, "rsi_period": 2, "rsi_buy": rb, "exit_sma": es, "trend_sma": int(base_params.get("trend_sma", 200))}
            for rb in (8, 12, 18)
            for es in (3, 5, 8)
        ]
        return combos
    if strategy_type == "vwap_rsi_reversion":
        return [
            {**base_params, "rsi_oversold": ro, "vwap_dev_pct": vd}
            for ro in (25, 30, 35)
            for vd in (0.3, 0.45, 0.7)
        ]
    if strategy_type == "shock_reversion":
        return [
            {**base_params, "shock_atr_mult": am, "vol_mult": vm}
            for am in (1.4, 1.8, 2.2)
            for vm in (1.6, 2.0, 2.5)
        ]
    # sma_crossover
    pairs = [(5, 15), (10, 30), (20, 50), (10, 50)]
    if is_bar_count >= 120:
        pairs.append((20, 100))
    if is_bar_count >= 220:
        pairs.append((50, 200))
    return [{**base_params, "fast_period": f, "slow_period": s} for f, s in pairs]


def run_walk_forward(bars: list[PriceBar], req: WalkForwardRequest) -> WalkForwardResult:
    """Split bars into In-Sample (tuning) and Out-of-Sample (untouched test) windows."""
    if len(bars) < 60:
        raise ValueError(
            f"Need at least 60 cached bars for walk-forward testing on {req.symbol} ({req.timeframe}); found {len(bars)}."
        )

    assert req.timeframe is not None
    split_idx = int(len(bars) * req.split_ratio)
    split_idx = max(30, min(len(bars) - 30, split_idx))
    is_bars = bars[:split_idx]
    oos_bars = bars[split_idx:]

    base_req_data = req.model_dump(exclude={"split_ratio", "auto_tune"})
    candidates = (
        _candidate_param_grid(req.strategy_type, len(is_bars), req.params)
        if req.auto_tune
        else [dict(req.params)]
    )

    best_is: BacktestResult | None = None
    best_params: dict[str, Any] = dict(req.params)
    best_score: tuple[int, float, float] = (-1, -999.0, -999.0)

    for cand in candidates:
        cand_req = BacktestRequest(**{**base_req_data, "params": cand})
        res = run_backtest(is_bars, cand_req)
        traded = 1 if res.total_trades > 0 else 0
        score = (traded, res.sharpe_ratio, res.total_return_pct)
        if best_is is None or score > best_score:
            best_score = score
            best_is = res
            best_params = dict(cand)

    assert best_is is not None
    oos_req = BacktestRequest(**{**base_req_data, "params": best_params})
    oos_res = run_backtest(oos_bars, oos_req)

    verdict: WalkForwardVerdict
    if oos_res.total_trades == 0:
        verdict = "NO_TRADES"
        reason = (
            "No trades triggered in the out-of-sample window "
            f"({len(oos_bars)} bars starting {oos_bars[0].time})."
        )
    elif oos_res.total_return_pct <= 0 and best_is.total_return_pct > 0:
        verdict = "OVERFIT_WARNING"
        reason = (
            f"In-sample was profitable ({best_is.total_return_pct:+.2f}%), "
            f"but out-of-sample lost money ({oos_res.total_return_pct:+.2f}%)."
        )
    elif oos_res.total_return_pct > 0 and oos_res.sharpe_ratio >= max(0.5, best_is.sharpe_ratio * 0.6):
        verdict = "ROBUST"
        reason = (
            f"Held up out-of-sample ({oos_res.total_return_pct:+.2f}%, Sharpe {oos_res.sharpe_ratio:.2f}) "
            f"vs. in-sample ({best_is.total_return_pct:+.2f}%, Sharpe {best_is.sharpe_ratio:.2f})."
        )
    else:
        verdict = "DEGRADED"
        reason = (
            f"Out-of-sample performance degraded ({oos_res.total_return_pct:+.2f}%, Sharpe {oos_res.sharpe_ratio:.2f}) "
            f"relative to in-sample ({best_is.total_return_pct:+.2f}%, Sharpe {best_is.sharpe_ratio:.2f})."
        )

    return WalkForwardResult(
        strategy_type=req.strategy_type,
        symbol=req.symbol,
        timeframe=req.timeframe,
        split_ratio=req.split_ratio,
        split_time=oos_bars[0].time,
        auto_tuned=req.auto_tune,
        candidates_tested=len(candidates),
        selected_params=best_params,
        verdict=verdict,
        verdict_reason=reason,
        in_sample=best_is,
        out_of_sample=oos_res,
    )


def build_aligned_benchmark(
    bars: list[PriceBar],
    target_times: list[str],
    initial_capital: float,
    timeframe: Timeframe,
    label: str,
) -> tuple[BenchmarkMetrics, list[float]] | None:
    """Compute a Buy & Hold benchmark curve aligned to `target_times`."""
    if not bars or len(target_times) < 2:
        return None
    sorted_bars = sorted(bars, key=lambda b: b.time)
    values: list[float] = []
    b_idx = 0
    # Advance to the latest bar at or before target_times[0]
    while b_idx + 1 < len(sorted_bars) and sorted_bars[b_idx + 1].time <= target_times[0]:
        b_idx += 1
    start_px = sorted_bars[b_idx].close
    if start_px <= 0:
        return None
    for ts in target_times:
        while b_idx + 1 < len(sorted_bars) and sorted_bars[b_idx + 1].time <= ts:
            b_idx += 1
        values.append(round(initial_capital * (sorted_bars[b_idx].close / start_px), 2))
    ret_pct, max_dd, sharpe = _compute_curve_stats(values, initial_capital, timeframe)
    metrics = BenchmarkMetrics(
        label=label,
        final_equity=round(values[-1], 2),
        total_return_pct=round(ret_pct, 2),
        sharpe_ratio=round(sharpe, 2),
        max_drawdown_pct=round(max_dd, 2),
    )
    return metrics, values


def _filter_bars_by_date(
    bars_by_symbol: dict[str, list[PriceBar]],
    start_date: str | None,
    end_date: str | None,
) -> dict[str, list[PriceBar]]:
    if not start_date and not end_date:
        return bars_by_symbol
    filtered: dict[str, list[PriceBar]] = {}
    for sym, bars in bars_by_symbol.items():
        subset = [
            b
            for b in bars
            if (not start_date or b.time[:10] >= start_date)
            and (not end_date or b.time[:10] <= end_date)
        ]
        if subset:
            filtered[sym] = subset
    return filtered


def run_rotation_backtest(
    bars_by_symbol: dict[str, list[PriceBar]],
    req: RotationBacktestRequest,
) -> PortfolioBacktestResult:
    """Backtest monthly cross-sectional Dual-Momentum Rotation across a symbol universe."""
    max_lb = max(resolve_momentum_horizons(req.momentum_mode, req.lookback_days))
    min_needed = max(max_lb + 2, req.trend_sma + 1)
    # Keep pre-start_date bars for lookback & SMA warmup; only evaluate/trade from start_date onward.
    scoped_bars = _filter_bars_by_date(bars_by_symbol, None, req.end_date)
    if req.etfs_only:
        allowed = set(ETF_UNIVERSE)
        if req.defensive_symbol:
            allowed.add(req.defensive_symbol.strip().upper())
        scoped_bars = {sym: bars for sym, bars in scoped_bars.items() if sym in allowed}
    valid_bars = {sym: bars for sym, bars in scoped_bars.items() if len(bars) >= min_needed}
    if not valid_bars:
        raise ValueError(
            f"Need at least {min_needed} daily bars per symbol for rotation backtest."
        )

    time_index: dict[str, dict[str, int]] = {
        sym: {b.time: idx for idx, b in enumerate(bars)} for sym, bars in valid_bars.items()
    }
    all_times = sorted({b.time for bars in valid_bars.values() for b in bars})
    if req.start_date and not any(t[:10] >= req.start_date for t in all_times):
        raise ValueError(f"No bars found on or after start_date {req.start_date}.")

    cash = float(req.initial_capital)
    slip_mult = req.slippage_bps / 10_000.0
    pos_qty: dict[str, int] = {sym: 0 for sym in valid_bars}
    avg_entry: dict[str, float] = {sym: 0.0 for sym in valid_bars}
    entry_time: dict[str, str] = {sym: "" for sym in valid_bars}
    last_close: dict[str, float] = {sym: valid_bars[sym][0].close for sym in valid_bars}
    bars_held: dict[str, int] = {sym: 0 for sym in valid_bars}
    trades_by_sym: dict[str, list[PortfolioBacktestTrade]] = {sym: [] for sym in valid_bars}
    all_trades: list[PortfolioBacktestTrade] = []

    bench_sym = req.benchmark_symbol if req.benchmark_symbol in valid_bars else None
    bench_started = False
    bench_start = 0.0
    qqq_start = 0.0
    has_qqq = "QQQ" in valid_bars
    eq_weight_shares: dict[str, float] = {}

    equity_curve: list[EquityPoint] = []
    bench_values: list[float] = []
    qqq_values: list[float] = []
    last_rebalance_month: str | None = None
    max_concurrent = 0
    points_with_pos = 0

    def sell_shares(sym: str, qty_to_sell: int, raw_price: float, ts: str, reason: str) -> None:
        nonlocal cash
        if qty_to_sell <= 0 or pos_qty[sym] <= 0:
            return
        sell_qty = min(qty_to_sell, pos_qty[sym])
        fill_exit = round(raw_price * (1.0 - slip_mult), 4)
        comm = req.commission_per_share * sell_qty
        cash += sell_qty * fill_exit - comm
        ep = avg_entry[sym]
        net_pnl = (fill_exit - ep) * sell_qty - (2.0 * comm)
        ret_pct = ((fill_exit - ep) / ep * 100.0) if ep > 0 else 0.0
        trade = PortfolioBacktestTrade(
            bot_id="dual-momentum",
            symbol=sym,
            entry_time=entry_time[sym] or ts,
            exit_time=ts,
            side="BUY",
            quantity=sell_qty,
            entry_price=round(ep, 2),
            exit_price=round(fill_exit, 2),
            pnl=round(net_pnl, 2),
            return_pct=round(ret_pct, 2),
            exit_reason=reason,
        )
        trades_by_sym[sym].append(trade)
        all_trades.append(trade)
        pos_qty[sym] -= sell_qty
        if pos_qty[sym] == 0:
            avg_entry[sym] = 0.0
            entry_time[sym] = ""

    for ts in all_times:
        cur_indices: dict[str, int] = {}
        for sym, idx_map in time_index.items():
            idx = idx_map.get(ts)
            if idx is not None:
                cur_indices[sym] = idx
                last_close[sym] = valid_bars[sym][idx].close

        if req.start_date and ts[:10] < req.start_date:
            continue

        if not bench_started:
            bench_started = True
            if bench_sym:
                bench_start = last_close[bench_sym]
            else:
                eq_weight_shares = {
                    sym: (req.initial_capital / len(valid_bars)) / last_close[sym]
                    for sym in valid_bars
                    if last_close[sym] > 0
                }
            if has_qqq:
                qqq_start = last_close["QQQ"]

        cur_month = ts[:7]
        warmed = any(idx + 1 >= min_needed for idx in cur_indices.values())
        if warmed and cur_month != last_rebalance_month:
            targets, _ = compute_dual_momentum_targets(
                bars_by_symbol=valid_bars,
                index_by_symbol=cur_indices,
                top_n=req.top_n,
                lookback_days=req.lookback_days,
                trend_sma=req.trend_sma,
                defensive_symbol=req.defensive_symbol,
                momentum_mode=req.momentum_mode,
                volatility_adjusted=req.volatility_adjusted,
            )
            portfolio_equity = cash + sum(pos_qty[s] * last_close[s] for s in valid_bars)

            # 1. Sell exited or overweight positions first to free cash
            for sym in list(valid_bars):
                cur_q = pos_qty[sym]
                if cur_q <= 0:
                    continue
                target_w = targets.get(sym, 0.0)
                px = last_close[sym]
                target_q = int((portfolio_equity * target_w) // (px * (1.0 + slip_mult) + req.commission_per_share)) if px > 0 else 0
                if cur_q > target_q:
                    sell_shares(sym, cur_q - target_q, px, ts, "Monthly Rotation Exit")

            # 2. Buy new or underweight target positions
            for sym, target_w in targets.items():
                px = last_close.get(sym, 0.0)
                if px <= 0:
                    continue
                fill_entry = round(px * (1.0 + slip_mult), 4)
                per_share = fill_entry + req.commission_per_share
                target_q = int((portfolio_equity * target_w) // per_share)
                cur_q = pos_qty.get(sym, 0)
                if target_q > cur_q:
                    want_q = target_q - cur_q
                    affordable_q = int(cash // per_share)
                    buy_q = min(want_q, affordable_q)
                    if buy_q > 0:
                        cash -= buy_q * fill_entry + req.commission_per_share * buy_q
                        total_q = cur_q + buy_q
                        avg_entry[sym] = (
                            (cur_q * avg_entry[sym] + buy_q * fill_entry) / total_q
                        )
                        if cur_q == 0:
                            entry_time[sym] = ts
                        pos_qty[sym] = total_q

            last_rebalance_month = cur_month

        open_now = sum(1 for q in pos_qty.values() if q > 0)
        if open_now > 0:
            points_with_pos += 1
            if open_now > max_concurrent:
                max_concurrent = open_now
        for sym, q in pos_qty.items():
            if q > 0 and sym in cur_indices:
                bars_held[sym] += 1

        equity = cash + sum(pos_qty[s] * last_close[s] for s in valid_bars)
        if bench_sym and bench_start > 0:
            bench_eq = req.initial_capital * (last_close[bench_sym] / bench_start)
        else:
            bench_eq = sum(eq_weight_shares[s] * last_close[s] for s in eq_weight_shares)
        bench_values.append(round(bench_eq, 2))
        qqq_eq = (
            round(req.initial_capital * (last_close["QQQ"] / qqq_start), 2)
            if has_qqq and qqq_start > 0
            else None
        )
        if qqq_eq is not None:
            qqq_values.append(qqq_eq)
        equity_curve.append(
            EquityPoint(
                time=ts,
                equity=round(equity, 2),
                benchmark_equity=round(bench_eq, 2),
                qqq_equity=qqq_eq,
            )
        )

    final_ts = all_times[-1]
    for sym in list(valid_bars):
        if pos_qty[sym] > 0:
            sell_shares(sym, pos_qty[sym], last_close[sym], final_ts, "End of Backtest")
    if equity_curve:
        equity_curve[-1] = EquityPoint(
            time=final_ts,
            equity=round(cash, 2),
            benchmark_equity=bench_values[-1],
            qqq_equity=qqq_values[-1] if qqq_values else None,
        )

    strategy_values = [pt.equity for pt in equity_curve]
    final_equity = strategy_values[-1] if strategy_values else req.initial_capital
    total_return_pct, max_dd, sharpe = _compute_curve_stats(
        strategy_values, req.initial_capital, "1 day"
    )
    bench_final = bench_values[-1] if bench_values else req.initial_capital
    bench_return_pct, bench_max_dd, bench_sharpe = _compute_curve_stats(
        bench_values, req.initial_capital, "1 day"
    )
    exposure_pct = (points_with_pos / len(all_times) * 100.0) if all_times else 0.0
    win_rate, profit_factor = _summarize_trades(all_trades)

    bot_results: list[PortfolioBotResult] = []
    for sym, bars in valid_bars.items():
        sym_trades = trades_by_sym[sym]
        sym_wr, _ = _summarize_trades(sym_trades)
        sym_pnl = sum(t.pnl for t in sym_trades)
        sym_exp = (bars_held[sym] / len(bars) * 100.0) if bars else 0.0
        if sym_trades or bars_held[sym] > 0:
            bot_results.append(
                PortfolioBotResult(
                    bot_id=f"rot-{sym.lower()}",
                    symbol=sym,
                    strategy_type="dual_momentum",
                    timeframe="1 day",
                    bars_tested=len(bars),
                    total_trades=len(sym_trades),
                    win_rate_pct=round(sym_wr, 1),
                    net_pnl=round(sym_pnl, 2),
                    exposure_pct=round(sym_exp, 1),
                    skipped_for_cash=0,
                )
            )
    bot_results.sort(key=lambda r: r.net_pnl, reverse=True)

    bench_label = (
        f"Buy & Hold ({bench_sym})"
        if bench_sym
        else f"Equal-Weight Buy & Hold ({len(valid_bars)} symbols)"
    )

    primary_benchmark = BenchmarkMetrics(
        label=bench_label,
        final_equity=round(bench_final, 2),
        total_return_pct=round(bench_return_pct, 2),
        sharpe_ratio=round(bench_sharpe, 2),
        max_drawdown_pct=round(bench_max_dd, 2),
    )
    benchmarks: list[BenchmarkMetrics] = [primary_benchmark]
    if qqq_values and bench_sym != "QQQ":
        qqq_ret, qqq_dd, qqq_sharpe = _compute_curve_stats(
            qqq_values, req.initial_capital, "1 day"
        )
        benchmarks.append(
            BenchmarkMetrics(
                label="Buy & Hold (QQQ)",
                final_equity=round(qqq_values[-1], 2),
                total_return_pct=round(qqq_ret, 2),
                sharpe_ratio=round(qqq_sharpe, 2),
                max_drawdown_pct=round(qqq_dd, 2),
            )
        )

    return PortfolioBacktestResult(
        initial_capital=round(req.initial_capital, 2),
        final_equity=round(final_equity, 2),
        total_return_pct=round(total_return_pct, 2),
        sharpe_ratio=round(sharpe, 2),
        max_drawdown_pct=round(max_dd, 2),
        win_rate_pct=round(win_rate, 1),
        profit_factor=round(profit_factor, 2),
        total_trades=len(all_trades),
        exposure_pct=round(exposure_pct, 1),
        max_concurrent_positions=max_concurrent,
        skipped_for_cash=0,
        alpha_vs_benchmark_pct=round(total_return_pct - bench_return_pct, 2),
        benchmark=primary_benchmark,
        benchmarks=benchmarks,
        bot_results=bot_results,
        equity_curve=_downsample_curve(equity_curve),
        trades=all_trades,
    )


def run_rotation_walk_forward(
    bars_by_symbol: dict[str, list[PriceBar]],
    req: RotationWalkForwardRequest,
) -> RotationWalkForwardResult:
    """Split universe bars into In-Sample and Out-of-Sample windows for Dual-Momentum."""
    scoped_bars = _filter_bars_by_date(bars_by_symbol, req.start_date, req.end_date)
    all_times = sorted({b.time for bars in scoped_bars.values() for b in bars})
    if len(all_times) < 260:
        raise ValueError(
            f"Need at least 260 daily timeline bars for rotation walk-forward; found {len(all_times)}."
        )

    split_idx = int(len(all_times) * req.split_ratio)
    split_idx = max(140, min(len(all_times) - 140, split_idx))
    split_time = all_times[split_idx]
    is_end = all_times[split_idx - 1][:10]
    oos_start = split_time[:10]

    # Count pre-start_date warmup bars so indicators don't get truncated unnecessarily
    pre_bars = max(
        (
            sum(1 for b in bars if req.start_date and b.time[:10] < req.start_date)
            for bars in bars_by_symbol.values()
        ),
        default=0,
    )
    is_avail = pre_bars + split_idx
    oos_avail = pre_bars + len(all_times)

    base_data = req.model_dump(exclude={"split_ratio", "auto_tune", "start_date", "end_date"})
    if req.auto_tune:
        if req.momentum_mode != "single":
            candidates = [
                {
                    "lookback_days": req.lookback_days,
                    "top_n": tn,
                    "trend_sma": min(req.trend_sma, is_avail - 20),
                }
                for tn in (2, 3, 4)
            ]
        else:
            candidates = [
                {
                    "lookback_days": lb,
                    "top_n": tn,
                    "trend_sma": min(req.trend_sma, is_avail - 20),
                }
                for lb in (63, 126, 189)
                for tn in (2, 3, 4)
                if lb + 25 < is_avail
            ]
        if not candidates:
            candidates = [
                {
                    "lookback_days": req.lookback_days,
                    "top_n": req.top_n,
                    "trend_sma": min(req.trend_sma, is_avail - 20),
                }
            ]
    else:
        candidates = [
            {
                "lookback_days": req.lookback_days,
                "top_n": req.top_n,
                "trend_sma": min(req.trend_sma, is_avail - 20, oos_avail - 20),
            }
        ]

    best_is: PortfolioBacktestResult | None = None
    best_params: dict[str, Any] = candidates[0]
    best_score: tuple[int, float, float] = (-1, -999.0, -999.0)

    for cand in candidates:
        try:
            cand_req = RotationBacktestRequest(
                **{**base_data, **cand, "start_date": req.start_date, "end_date": is_end}
            )
            res = run_rotation_backtest(bars_by_symbol, cand_req)
        except ValueError:
            continue
        traded = 1 if res.total_trades > 0 else 0
        score = (traded, res.sharpe_ratio, res.total_return_pct)
        if best_is is None or score > best_score:
            best_score = score
            best_is = res
            best_params = dict(cand)

    if best_is is None:
        raise ValueError("No valid rotation candidate could run on the in-sample window.")

    oos_params = {
        **best_params,
        "trend_sma": min(best_params["trend_sma"], oos_avail - 15),
    }
    oos_res = run_rotation_backtest(
        bars_by_symbol,
        RotationBacktestRequest(
            **{**base_data, **oos_params, "start_date": oos_start, "end_date": req.end_date}
        ),
    )

    verdict: WalkForwardVerdict
    if oos_res.total_trades == 0:
        verdict = "NO_TRADES"
        reason = f"No rotation trades triggered out-of-sample starting {split_time}."
    elif oos_res.total_return_pct <= 0 and best_is.total_return_pct > 0:
        verdict = "OVERFIT_WARNING"
        reason = (
            f"In-sample gained {best_is.total_return_pct:+.2f}%, "
            f"but out-of-sample lost {oos_res.total_return_pct:+.2f}%."
        )
    elif oos_res.total_return_pct > 0 and oos_res.sharpe_ratio >= max(0.5, best_is.sharpe_ratio * 0.6):
        verdict = "ROBUST"
        reason = (
            f"Held up out-of-sample ({oos_res.total_return_pct:+.2f}%, Sharpe {oos_res.sharpe_ratio:.2f}) "
            f"vs. in-sample ({best_is.total_return_pct:+.2f}%, Sharpe {best_is.sharpe_ratio:.2f})."
        )
    else:
        verdict = "DEGRADED"
        reason = (
            f"Out-of-sample degraded ({oos_res.total_return_pct:+.2f}%, Sharpe {oos_res.sharpe_ratio:.2f}) "
            f"vs. in-sample ({best_is.total_return_pct:+.2f}%, Sharpe {best_is.sharpe_ratio:.2f})."
        )

    return RotationWalkForwardResult(
        split_ratio=req.split_ratio,
        split_time=split_time,
        auto_tuned=req.auto_tune,
        candidates_tested=len(candidates),
        selected_params=best_params,
        verdict=verdict,
        verdict_reason=reason,
        in_sample=best_is,
        out_of_sample=oos_res,
    )
