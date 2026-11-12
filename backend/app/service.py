"""Core application service tying together broker, persistence, backtesting, risk, and bots."""

from __future__ import annotations

from collections import deque

from app.backtest.data import HistoricalDataService
from app.backtest.engine import (
    build_aligned_benchmark,
    run_backtest as execute_backtest,
    run_portfolio_backtest as execute_portfolio_backtest,
    run_rotation_backtest as execute_rotation_backtest,
    run_rotation_walk_forward as execute_rotation_walk_forward,
    run_walk_forward as execute_walk_forward,
)
from app.broker.base import BrokerClient, BrokerError
from app.broker.ibkr_client import IBKRClient
from app.broker.paper_simulator import PaperSimulator
from app.config import ETF_UNIVERSE, STARTER_UNIVERSE, Settings
from app.market.calendar import USMarketCalendar
from app.market.news_guard import NewsGuard
from app.models import (
    AccountSummary,
    ActivityEvent,
    BacktestRequest,
    BacktestResult,
    BotBudgetPatch,
    BotCreateRequest,
    BotDryRunPreview,
    BotPlannedOrder,
    BrokerMode,
    ConnectionStatus,
    CurrencyConversionRequest,
    CurrencyConversionResult,
    DashboardSnapshot,
    DataSyncRequest,
    DualMomentumAssetScore,
    DualMomentumPreview,
    FxRateResponse,
    MarketStatus,
    NewsCheckResult,
    OrderRecord,
    OrderRequest,
    PortfolioBacktestRequest,
    PortfolioBacktestResult,
    Position,
    PriceBar,
    Quote,
    ReconciliationReport,
    RiskLimits,
    RiskLimitsPatch,
    RiskStatus,
    RotationBacktestRequest,
    RotationWalkForwardRequest,
    RotationWalkForwardResult,
    StrategyInfo,
    StrategyStartRequest,
    Timeframe,
    UniverseResponse,
    WalkForwardRequest,
    WalkForwardResult,
)
from app.risk.guard import RiskGuard, RiskViolation
from app.storage.db import TradingStore
from app.strategies.dual_momentum import (
    rank_dual_momentum_universe,
    resolve_momentum_horizons,
)
from app.strategies.manager import StrategyManager


class TradingService:
    """Facade coordinating broker access, SQLite persistence, backtesting, risk, and bots."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._store = TradingStore(settings.db_path)
        self._data_service = HistoricalDataService(self._store)
        self._calendar = USMarketCalendar(enforce_market_hours=settings.enforce_market_hours)
        self._news_guard = NewsGuard(
            ttl_seconds=900,
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            use_llm=settings.news_guard_use_llm,
            llm_timeout_seconds=settings.news_guard_llm_timeout_seconds,
        )
        self._simulator = PaperSimulator(initial_cash=100_000.0)
        persisted_mode = self._store.get_config("broker_mode")
        persisted_host = self._store.get_config("ibkr_host")
        persisted_port = self._store.get_config("ibkr_port")
        persisted_client_id = self._store.get_config("ibkr_client_id")

        self._ibkr = IBKRClient(
            host=persisted_host or settings.ibkr_host,
            port=int(persisted_port) if persisted_port else settings.ibkr_port,
            client_id=int(persisted_client_id) if persisted_client_id else settings.ibkr_client_id,
            allow_live_trading=settings.allow_live_trading,
        )
        self._mode: BrokerMode = (
            persisted_mode if persisted_mode in ("simulator", "ibkr") else settings.broker_mode
        )  # type: ignore[assignment]
        if self._mode == "ibkr":
            self._ibkr._want_connected = True
        self._watchlist: list[str] = list(settings.watchlist_symbols)
        self._risk = RiskGuard(
            RiskLimits(
                max_order_qty=settings.max_order_qty,
                max_order_notional=settings.max_order_notional,
                max_position_shares=settings.max_position_shares,
                max_daily_loss=settings.max_daily_loss,
                allow_live_trading=settings.allow_live_trading,
            )
        )
        self._strategies = StrategyManager()
        self._restore_strategies()

        stored_events = self._store.list_activity(limit=80)
        self._activity: deque[ActivityEvent] = deque(stored_events, maxlen=100)
        self._reconciliation: ReconciliationReport | None = None
        self._tick_count = 0

        self.log_activity(
            "info",
            "system",
            f"Trading engine initialized in {self._mode.upper()} mode.",
        )

    def _restore_strategies(self) -> None:
        saved = self._store.load_bots()
        if not saved:
            # First-run fresh DB: persist the initial default fleet once
            self._persist_strategies()
            return
        # Respect user's saved fleet (so deleted default bots are not re-added on restart)
        self._strategies._strategies.clear()
        for row in saved:
            self._strategies.load_or_create(
                bot_id=row["bot_id"],
                strategy_type=row["strategy_type"],
                symbol=row["symbol"],
                timeframe=row["timeframe"],
                stop_loss_pct=row["stop_loss_pct"],
                take_profit_pct=row["take_profit_pct"],
                params=row["params"],
                trades_placed=row["trades_placed"],
                last_signal=row["last_signal"],
                last_checked_at=row["last_checked_at"],
            )

    @property
    def broker(self) -> BrokerClient:
        return self._simulator if self._mode == "simulator" else self._ibkr

    @property
    def watchlist(self) -> list[str]:
        return list(self._watchlist)

    def market_status(self) -> MarketStatus:
        return self._calendar.status()

    async def check_news(self, symbol: str, force_refresh: bool = False) -> NewsCheckResult:
        return await self._news_guard.check(symbol, force_refresh=force_refresh)

    def universe_status(self) -> UniverseResponse:
        return UniverseResponse(
            symbols=list(STARTER_UNIVERSE),
            cache=self._store.list_bar_cache_summaries(),
        )

    async def sync_historical_data(self, req: DataSyncRequest) -> dict[str, int]:
        targets = [s.strip().upper() for s in (req.symbols or STARTER_UNIVERSE) if s.strip()]
        counts: dict[str, int] = {}
        for sym in targets:
            count = await self._data_service.sync_symbol(
                symbol=sym,
                timeframe=req.timeframe,
                source=req.source,
                lookback=req.lookback,
                ibkr_client=self._ibkr if req.source == "ibkr" else None,
            )
            counts[sym] = count
        total = sum(counts.values())
        self.log_activity(
            "success",
            "data",
            f"Synced {total} {req.timeframe} bar(s) from {req.source} across {len(targets)} symbol(s).",
        )
        return counts

    async def _attach_index_benchmarks(
        self,
        result: BacktestResult | PortfolioBacktestResult,
        timeframe: Timeframe,
    ) -> None:
        """Append Buy & Hold (SPY) and Buy & Hold (QQQ) benchmarks to a backtest result."""
        if len(result.equity_curve) < 2:
            return
        result.benchmarks = [result.benchmark] if result.benchmark else []
        seen_labels = {b.label for b in result.benchmarks}
        target_times = [pt.time for pt in result.equity_curve]

        for sym, label in (("SPY", "Buy & Hold (SPY)"), ("QQQ", "Buy & Hold (QQQ)")):
            bars = self._data_service.get_cached_bars(sym, timeframe)
            if len(bars) < 20:
                try:
                    lookback = (
                        "20y"
                        if timeframe == "1 day"
                        else ("730d" if timeframe == "1 hour" else "60d")
                    )
                    await self._data_service.sync_symbol(
                        symbol=sym, timeframe=timeframe, source="yfinance", lookback=lookback
                    )
                    bars = self._data_service.get_cached_bars(sym, timeframe)
                except Exception:
                    bars = []
            built = build_aligned_benchmark(
                bars, target_times, result.initial_capital, timeframe, label
            )
            if built is None:
                continue
            metrics, values = built
            if label not in seen_labels:
                result.benchmarks.append(metrics)
                seen_labels.add(label)
            if sym == "QQQ":
                for pt, val in zip(result.equity_curve, values):
                    pt.qqq_equity = val

    async def run_backtest(self, req: BacktestRequest) -> BacktestResult:
        bars = self._data_service.get_cached_bars(req.symbol, req.timeframe)
        if len(bars) < 20:
            # Auto-fetch via yfinance on first backtest if cache is empty
            await self._data_service.sync_symbol(
                symbol=req.symbol,
                timeframe=req.timeframe,
                source="yfinance",
                lookback="20y" if req.timeframe == "1 day" else "60d",
            )
            bars = self._data_service.get_cached_bars(req.symbol, req.timeframe)

        result = execute_backtest(bars, req)
        await self._attach_index_benchmarks(result, req.timeframe)
        self.log_activity(
            "info",
            "backtest",
            f"Backtest {req.strategy_type} on {req.symbol} ({req.timeframe}): "
            f"{result.total_return_pct:+.2f}% | Sharpe {result.sharpe_ratio:.2f} | {result.total_trades} trades",
        )
        return result

    async def run_portfolio_backtest(
        self, req: PortfolioBacktestRequest
    ) -> PortfolioBacktestResult:
        bars_by_bot: dict[str, list[PriceBar]] = {}
        for bot in req.bots:
            assert bot.bot_id is not None
            assert bot.timeframe is not None
            bars = self._data_service.get_cached_bars(bot.symbol, bot.timeframe)
            if len(bars) < 20:
                lookback = (
                    "20y"
                    if bot.timeframe == "1 day"
                    else ("730d" if bot.timeframe == "1 hour" else "60d")
                )
                await self._data_service.sync_symbol(
                    symbol=bot.symbol,
                    timeframe=bot.timeframe,
                    source="yfinance",
                    lookback=lookback,
                )
                bars = self._data_service.get_cached_bars(bot.symbol, bot.timeframe)
            bars_by_bot[bot.bot_id] = bars

        result = execute_portfolio_backtest(bars_by_bot, req)
        tf: Timeframe = req.bots[0].timeframe or "1 day" if req.bots else "1 day"
        await self._attach_index_benchmarks(result, tf)
        self.log_activity(
            "info",
            "backtest",
            f"Portfolio backtest ({len(req.bots)} bots): "
            f"{result.total_return_pct:+.2f}% | Sharpe {result.sharpe_ratio:.2f} | "
            f"MaxDD -{result.max_drawdown_pct:.2f}% | {result.total_trades} trades",
        )
        return result

    async def run_walk_forward(self, req: WalkForwardRequest) -> WalkForwardResult:
        assert req.timeframe is not None
        bars = self._data_service.get_cached_bars(req.symbol, req.timeframe)
        if len(bars) < 60:
            lookback = (
                "20y"
                if req.timeframe == "1 day"
                else ("730d" if req.timeframe == "1 hour" else "60d")
            )
            await self._data_service.sync_symbol(
                symbol=req.symbol,
                timeframe=req.timeframe,
                source="yfinance",
                lookback=lookback,
            )
            bars = self._data_service.get_cached_bars(req.symbol, req.timeframe)

        result = execute_walk_forward(bars, req)
        await self._attach_index_benchmarks(result.in_sample, req.timeframe)
        await self._attach_index_benchmarks(result.out_of_sample, req.timeframe)
        self.log_activity(
            "info",
            "backtest",
            f"Walk-forward {req.strategy_type} on {req.symbol} ({req.timeframe}): "
            f"{result.verdict} | OOS {result.out_of_sample.total_return_pct:+.2f}% "
            f"(Sharpe {result.out_of_sample.sharpe_ratio:.2f})",
        )
        return result

    async def _load_universe_daily_bars(
        self, symbols: list[str] | None, defensive_symbol: str | None
    ) -> dict[str, list[PriceBar]]:
        targets = [s.strip().upper() for s in (symbols or STARTER_UNIVERSE) if s.strip()]
        if defensive_symbol and defensive_symbol not in targets:
            targets.append(defensive_symbol)

        bars_by_symbol: dict[str, list[PriceBar]] = {}
        for sym in targets:
            bars = self._data_service.get_cached_bars(sym, "1 day")
            if len(bars) < 210:
                await self._data_service.sync_symbol(
                    symbol=sym,
                    timeframe="1 day",
                    source="yfinance",
                    lookback="20y",
                )
                bars = self._data_service.get_cached_bars(sym, "1 day")
            bars_by_symbol[sym] = bars
        return bars_by_symbol

    async def run_rotation_backtest(
        self, req: RotationBacktestRequest
    ) -> PortfolioBacktestResult:
        base_syms = list(ETF_UNIVERSE) if req.etfs_only else req.symbols
        bars_by_symbol = await self._load_universe_daily_bars(
            base_syms, req.defensive_symbol
        )
        result = execute_rotation_backtest(bars_by_symbol, req)
        self.log_activity(
            "info",
            "backtest",
            f"Dual-Momentum Rotation (Top {req.top_n}, {req.lookback_days}d): "
            f"{result.total_return_pct:+.2f}% | Sharpe {result.sharpe_ratio:.2f} | "
            f"MaxDD -{result.max_drawdown_pct:.2f}%",
        )
        return result

    async def run_rotation_walk_forward(
        self, req: RotationWalkForwardRequest
    ) -> RotationWalkForwardResult:
        base_syms = list(ETF_UNIVERSE) if req.etfs_only else req.symbols
        bars_by_symbol = await self._load_universe_daily_bars(
            base_syms, req.defensive_symbol
        )
        result = execute_rotation_walk_forward(bars_by_symbol, req)
        self.log_activity(
            "info",
            "backtest",
            f"Rotation Walk-Forward: {result.verdict} | OOS {result.out_of_sample.total_return_pct:+.2f}% "
            f"(Sharpe {result.out_of_sample.sharpe_ratio:.2f})",
        )
        return result

    async def preview_dual_momentum(
        self,
        top_n: int | None = None,
        lookback_days: int | None = None,
        momentum_mode: str | None = None,
        etfs_only: bool | None = None,
        volatility_adjusted: bool | None = None,
        trend_sma: int | None = None,
        defensive_symbol: str | None = None,
    ) -> DualMomentumPreview:
        """Dry-run Dual-Momentum ranking across the universe without placing orders."""
        dm_bot = next(
            (s for s in self._strategies.list_strategies() if s.strategy_type == "dual_momentum"),
            None,
        )
        bot_params = dm_bot.params if dm_bot is not None else {}
        top_n = top_n if top_n is not None else max(1, int(bot_params.get("top_n", 3)))
        lookback_days = (
            lookback_days
            if lookback_days is not None
            else max(20, int(bot_params.get("lookback_days", 126)))
        )
        resolved_mode = momentum_mode or str(bot_params.get("momentum_mode", "single"))
        resolved_etfs_only = (
            etfs_only if etfs_only is not None else bool(bot_params.get("etfs_only", False))
        )
        resolved_vol_adj = (
            volatility_adjusted
            if volatility_adjusted is not None
            else bool(bot_params.get("volatility_adjusted", False))
        )
        trend_sma = (
            trend_sma if trend_sma is not None else max(20, int(bot_params.get("trend_sma", 200)))
        )
        if defensive_symbol is None:
            raw_def = bot_params.get("defensive_symbol", "TLT")
            defensive_symbol = str(raw_def).strip().upper() or None if raw_def is not None else None
        else:
            defensive_symbol = defensive_symbol.strip().upper() or None

        max_lb = max(resolve_momentum_horizons(resolved_mode, lookback_days))
        needed = max(max_lb + 2, trend_sma + 5)
        bars_by_symbol: dict[str, list[PriceBar]] = {}
        source_label = "sqlite_yfinance"
        base_universe = list(ETF_UNIVERSE if resolved_etfs_only else STARTER_UNIVERSE)

        if self._mode == "ibkr" and self.broker.status().connected:
            source_label = "ibkr"
            targets = list(base_universe)
            if defensive_symbol and defensive_symbol not in targets:
                targets.append(defensive_symbol)
            for sym in targets:
                bars = await self.broker.get_historical_bars(
                    sym, count=needed, timeframe="1 day"
                )
                if bars:
                    bars_by_symbol[sym] = bars
        else:
            bars_by_symbol = await self._load_universe_daily_bars(
                base_universe, defensive_symbol
            )

        index_by_symbol = {sym: len(bars) - 1 for sym, bars in bars_by_symbol.items() if bars}
        targets_map, summary, rows = rank_dual_momentum_universe(
            bars_by_symbol=bars_by_symbol,
            index_by_symbol=index_by_symbol,
            top_n=top_n,
            lookback_days=lookback_days,
            trend_sma=trend_sma,
            defensive_symbol=defensive_symbol,
            momentum_mode=resolved_mode,
            volatility_adjusted=resolved_vol_adj,
        )

        account_equity = 10_000.0
        if self.broker.status().connected:
            try:
                acct = await self.broker.get_account_summary()
                if acct.net_liquidation > 0:
                    account_equity = acct.net_liquidation
            except Exception:
                pass

        dm_bot = next(
            (s for s in self._strategies.list_strategies() if s.strategy_type == "dual_momentum"),
            None,
        )
        if dm_bot is not None:
            pct = dm_bot.params.get("max_allocation_pct")
            cap = dm_bot.params.get("max_capital")
            if pct is not None and float(pct) > 0:
                account_equity = min(account_equity, account_equity * (float(pct) / 100.0))
            if cap is not None and float(cap) > 0:
                account_equity = min(account_equity, float(cap))

        rankings = [
            DualMomentumAssetScore(
                symbol=r["symbol"],
                trailing_return_pct=r["trailing_return_pct"],
                last_close=r["last_close"],
                trend_sma=r["trend_sma"],
                passes_filter=r["passes_filter"],
                is_defensive=r["is_defensive"],
                target_weight_pct=r["target_weight_pct"],
                estimated_shares=(
                    int((account_equity * (r["target_weight_pct"] / 100.0)) // r["last_close"])
                    if r["target_weight_pct"] > 0 and r["last_close"] > 0
                    else 0
                ),
            )
            for r in rows
        ]

        as_of = max(
            (bars[-1].time for bars in bars_by_symbol.values() if bars),
            default="",
        )
        return DualMomentumPreview(
            as_of=as_of,
            source=source_label,
            top_n=top_n,
            lookback_days=lookback_days,
            momentum_mode=resolved_mode,  # type: ignore[arg-type]
            etfs_only=resolved_etfs_only,
            volatility_adjusted=resolved_vol_adj,
            trend_sma=trend_sma,
            defensive_symbol=defensive_symbol,
            account_equity=round(account_equity, 2),
            summary=summary,
            targets={k: round(v * 100.0, 1) for k, v in targets_map.items()},
            rankings=rankings,
        )

    def add_watchlist_symbol(self, symbol: str) -> list[str]:
        cleaned = symbol.strip().upper()
        if cleaned and cleaned not in self._watchlist:
            self._watchlist.append(cleaned)
            self.log_activity("info", "market", f"Added {cleaned} to watchlist.")
        return self.watchlist

    def remove_watchlist_symbol(self, symbol: str) -> list[str]:
        cleaned = symbol.strip().upper()
        if cleaned in self._watchlist:
            self._watchlist.remove(cleaned)
        return self.watchlist

    def log_activity(self, level: str, category: str, message: str) -> ActivityEvent:
        evt = self._store.append_activity(level, category, message)
        self._activity.appendleft(evt)
        return evt

    def connection_status(self) -> ConnectionStatus:
        return self.broker.status()

    async def reconcile_broker_state(self) -> ReconciliationReport:
        """Compare broker positions & open orders against persisted SQLite order records."""
        if not self.broker.status().connected:
            raise BrokerError("Cannot reconcile while disconnected.")

        positions = await self.broker.get_positions()
        broker_orders = await self.broker.get_orders()
        broker_by_id = {o.order_id: o for o in broker_orders}
        discrepancies: list[str] = []
        updated = 0

        for ord_rec in broker_orders:
            self._store.upsert_order(ord_rec, self._mode)
            updated += 1

        stored_orders = self._store.list_orders(broker_mode=self._mode, limit=200)
        for prev in stored_orders:
            if prev.status in ("PendingSubmit", "Submitted") and prev.order_id not in broker_by_id:
                discrepancies.append(
                    f"Stored open order #{prev.order_id} ({prev.symbol}) not found on broker; marked Cancelled."
                )
                prev.status = "Cancelled"
                prev.message = "Reconciled missing on broker"
                self._store.upsert_order(prev, self._mode)

        open_count = sum(
            1 for o in broker_orders if o.status in ("PendingSubmit", "Submitted")
        )
        report = ReconciliationReport(
            broker_positions=len(positions),
            broker_open_orders=open_count,
            db_orders_updated=updated,
            discrepancies=discrepancies,
        )
        self._reconciliation = report
        self.log_activity(
            "warning" if discrepancies else "info",
            "reconcile",
            f"Reconciled {len(positions)} position(s), {open_count} open order(s)"
            + (f" ({len(discrepancies)} discrepancy)" if discrepancies else "."),
        )
        return report

    async def connect(self) -> ConnectionStatus:
        status = await self.broker.connect()
        self.log_activity(
            "success",
            "connection",
            f"Connected to {status.environment} ({status.broker_mode}).",
        )
        await self.reconcile_broker_state()
        return status

    async def disconnect(self) -> ConnectionStatus:
        self._strategies.stop_all()
        self._persist_strategies()
        status = await self.broker.disconnect()
        self.log_activity("warning", "connection", "Disconnected from broker.")
        return status

    async def switch_mode(
        self,
        mode: BrokerMode,
        host: str | None = None,
        port: int | None = None,
        client_id: int | None = None,
    ) -> ConnectionStatus:
        if self.broker.status().connected:
            await self.disconnect()

        self._mode = mode
        self._store.set_config("broker_mode", mode)
        if mode == "ibkr":
            h = host or self._settings.ibkr_host
            p = port or self._settings.ibkr_port
            cid = client_id or self._settings.ibkr_client_id
            self._store.set_config("ibkr_host", h)
            self._store.set_config("ibkr_port", str(p))
            self._store.set_config("ibkr_client_id", str(cid))
            self._ibkr = IBKRClient(
                host=h,
                port=p,
                client_id=cid,
                allow_live_trading=self._settings.allow_live_trading,
            )
            self.log_activity(
                "info",
                "connection",
                f"Switched to IBKR mode ({h}:{p}, clientId={cid}).",
            )
            return self._ibkr.status()

        status = await self._simulator.connect()
        self.log_activity("info", "connection", "Switched to Simulator mode.")
        await self.reconcile_broker_state()
        return status

    async def submit_order(self, order: OrderRequest) -> OrderRecord:
        if not self.broker.status().connected:
            raise BrokerError("Broker is not connected.")

        if (
            self._mode == "ibkr"
            and self._settings.enforce_market_hours
            and order.source.startswith("strategy:")
        ):
            mkt = self.market_status()
            if not mkt.is_rth_open:
                raise RiskViolation(
                    f"US Market Hours guard blocked automated order ({mkt.session}: {mkt.reason})."
                )

        positions = await self.broker.get_positions()
        account = await self.broker.get_account_summary()
        quotes = await self.broker.get_quotes([order.symbol])
        ref_price = quotes[0].last if quotes else None

        self._risk.validate_order(
            order=order,
            reference_price=ref_price,
            positions=positions,
            account=account,
        )

        record = await self.broker.place_order(order)
        self._store.upsert_order(record, self._mode)
        bracket_note = ""
        if record.stop_loss_price or record.take_profit_price:
            bracket_note = f" [SL={record.stop_loss_price or '-'} TP={record.take_profit_price or '-'}]"
        self.log_activity(
            "success" if record.status == "Filled" else "info",
            "order",
            f"{record.side} {record.quantity} {record.symbol} ({record.order_type}){bracket_note} -> {record.status}",
        )
        return record

    async def cancel_order(self, order_id: int) -> OrderRecord:
        record = await self.broker.cancel_order(order_id)
        self._store.upsert_order(record, self._mode)
        self.log_activity("warning", "order", f"Cancelled order #{order_id}.")
        return record

    async def get_fx_rate(self, pair: str = "USDSGD") -> FxRateResponse:
        return await self.broker.get_fx_rate(pair)

    async def convert_currency(
        self, req: CurrencyConversionRequest
    ) -> CurrencyConversionResult:
        if self._risk.trading_halted:
            raise RiskViolation(
                f"Trading is halted ({self._risk.status().halt_reason or 'kill switch active'}). Currency conversion blocked."
            )
        result = await self.broker.convert_currency(req)
        if result.success:
            self.log_activity(
                "success",
                "fx",
                f"Currency Conversion: {result.from_amount:,.2f} {result.from_currency} -> "
                f"{result.to_amount:,.2f} {result.to_currency} via {result.exchange} (Rate: {result.rate:.4f})",
            )
            if result.order_id:
                order_rec = OrderRecord(
                    order_id=result.order_id,
                    symbol="USD.SGD",
                    side="BUY" if result.from_currency == "SGD" else "SELL",
                    quantity=int(
                        round(
                            result.to_amount
                            if result.from_currency == "SGD"
                            else result.from_amount
                        )
                    ),
                    filled_quantity=int(
                        round(
                            result.to_amount
                            if result.from_currency == "SGD"
                            else result.from_amount
                        )
                    )
                    if result.status == "Filled"
                    else 0,
                    order_type=req.order_type,
                    limit_price=req.limit_price,
                    avg_fill_price=result.rate,
                    status=result.status,  # type: ignore[arg-type]
                    source="fx:convert",
                    message=result.message,
                    created_at=result.created_at,
                    updated_at=result.created_at,
                )
                self._store.upsert_order(order_rec, self._mode)
        return result

    async def set_kill_switch(
        self, halted: bool, cancel_open_orders: bool = True, reason: str | None = None
    ) -> RiskStatus:
        status = self._risk.set_kill_switch(halted, reason)
        if halted:
            self._strategies.stop_all()
            self._persist_strategies()
            cancelled = 0
            if cancel_open_orders and self.broker.status().connected:
                cancelled = await self.broker.cancel_all_orders()
            self.log_activity(
                "error",
                "risk",
                f"KILL SWITCH ENGAGED ({status.halt_reason}). Cancelled {cancelled} open order(s).",
            )
        else:
            self.log_activity("success", "risk", "Kill switch cleared. Trading resumed.")
        return status

    def update_risk_limits(self, patch: RiskLimitsPatch) -> RiskStatus:
        status = self._risk.update_limits(patch)
        self.log_activity("info", "risk", "Updated pre-trade risk limits.")
        return status

    def risk_status(self) -> RiskStatus:
        return self._risk.status()

    def list_strategies(self) -> list[StrategyInfo]:
        return self._strategies.list_strategies()

    def _persist_strategies(self) -> None:
        for s in self._strategies.list_strategies():
            self._store.save_bot(
                bot_id=s.name,
                strategy_type=s.strategy_type,
                symbol=s.symbol,
                timeframe=s.timeframe,
                running=s.running,
                stop_loss_pct=s.stop_loss_pct,
                take_profit_pct=s.take_profit_pct,
                params=s.params,
                last_signal=s.last_signal,
                last_checked_at=s.last_checked_at,
                trades_placed=s.trades_placed,
            )

    def create_bot(self, req: BotCreateRequest) -> StrategyInfo:
        info = self._strategies.create_bot(req)
        self._persist_strategies()
        self.log_activity(
            "info",
            "strategy",
            f"Created bot '{info.name}' ({info.strategy_type}) on {info.symbol}.",
        )
        return info

    def update_bot_budget(self, bot_id: str, patch: BotBudgetPatch) -> StrategyInfo:
        info = self._strategies.update_budget(
            bot_id=bot_id,
            quantity=patch.quantity,
            max_allocation_pct=patch.max_allocation_pct,
            max_capital=patch.max_capital,
            params=patch.params,
            stop_loss_pct=patch.stop_loss_pct,
            take_profit_pct=patch.take_profit_pct,
            update_sl="stop_loss_pct" in patch.model_fields_set,
            update_tp="take_profit_pct" in patch.model_fields_set,
        )
        self._persist_strategies()
        cap_parts: list[str] = []
        if patch.max_allocation_pct is not None:
            cap_parts.append(f"max {patch.max_allocation_pct:.1f}% equity")
        if patch.max_capital is not None:
            cap_parts.append(f"max ${patch.max_capital:,.0f}")
        if patch.quantity is not None:
            cap_parts.append(f"default {patch.quantity} sh")
        summary = ", ".join(cap_parts) if cap_parts else "uncapped"
        self.log_activity("info", "strategy", f"Updated budget for bot '{bot_id}': {summary}.")
        return info

    async def preview_bot(self, bot_id: str) -> BotDryRunPreview:
        """Dry-run any bot's evaluate() without placing orders or mutating bot state."""
        bot = self._strategies._strategies.get(bot_id)
        if bot is None:
            raise KeyError(f"Unknown bot '{bot_id}'")
        if not self.broker.status().connected:
            raise BrokerError("Connect broker / simulator first to preview bot orders.")

        captured: list[OrderRequest] = []

        async def _capture_order(req: OrderRequest) -> OrderRecord:
            captured.append(req)
            return OrderRecord(
                order_id=0,
                symbol=req.symbol,
                side=req.side,
                quantity=req.quantity,
                order_type=req.order_type,
                limit_price=req.limit_price,
                stop_price=req.stop_price,
                stop_loss_price=req.stop_loss_price,
                take_profit_price=req.take_profit_price,
                tif=req.tif,
                status="Submitted",
                source=req.source or f"strategy:{bot_id}",
            )

        def _noop_log(_level: str, _category: str, _message: str) -> None:
            return None

        prev_mono = bot._last_entry_monotonic
        prev_trades_placed = bot.trades_placed
        has_regime = hasattr(bot, "_last_regime")
        prev_regime = getattr(bot, "_last_regime", None)
        has_rebalance = hasattr(bot, "_last_rebalance_month")
        prev_month = getattr(bot, "_last_rebalance_month", None)
        prev_summary = getattr(bot, "_last_rebalance_summary", "")
        if has_rebalance:
            setattr(bot, "_last_rebalance_month", None)

        try:
            signal = await bot.evaluate(
                self.broker, _capture_order, _noop_log, news_checker=self.check_news
            )
        finally:
            bot._last_entry_monotonic = prev_mono
            bot.trades_placed = prev_trades_placed
            if has_regime:
                setattr(bot, "_last_regime", prev_regime)
            if has_rebalance:
                setattr(bot, "_last_rebalance_month", prev_month)
                setattr(bot, "_last_rebalance_summary", prev_summary)

        mkt = self.market_status()
        positions = await self.broker.get_positions()
        account = await self.broker.get_account_summary()
        planned: list[BotPlannedOrder] = []
        for req in captured:
            ref_px = req.limit_price
            if ref_px is None:
                try:
                    quotes = await self.broker.get_quotes([req.symbol])
                    if quotes:
                        ref_px = quotes[0].last
                except Exception:
                    ref_px = None
            notional = round(ref_px * req.quantity, 2) if ref_px is not None else None
            risk_msg = "PASS"
            try:
                self._risk.validate_order(req, ref_px, positions, account)
            except RiskViolation as exc:
                risk_msg = f"BLOCKED: {exc}"
            planned.append(
                BotPlannedOrder(
                    symbol=req.symbol,
                    side=req.side,
                    quantity=req.quantity,
                    order_type=req.order_type,
                    limit_price=req.limit_price,
                    stop_loss_price=req.stop_loss_price,
                    take_profit_price=req.take_profit_price,
                    estimated_price=round(ref_px, 2) if ref_px is not None else None,
                    estimated_notional=notional,
                    risk_status=risk_msg,
                    rationale=self._explain_planned_order(bot, req),
                )
            )

        return BotDryRunPreview(
            bot_id=bot.name,
            strategy_type=bot.strategy_type,
            symbol=bot.symbol,
            timeframe=bot.timeframe,
            signal=signal,
            rationale=self._explain_bot_preview(bot, signal, planned),
            market_session=mkt.session,
            market_note=mkt.reason,
            orders=planned,
        )

    @staticmethod
    def _explain_planned_order(bot: Any, req: OrderRequest) -> str:
        """Return a plain-English rationale for a single planned order."""
        st = bot.strategy_type
        collar = float(bot.params.get("limit_collar_pct", 0.25))
        order_desc = (
            f"collared limit (±{collar:.2f}%)"
            if req.order_type == "LMT" and req.limit_price is not None
            else "market order"
        )
        if st == "dual_momentum":
            defensive = str(bot.params.get("defensive_symbol") or "").strip().upper()
            lookback = int(bot.params.get("lookback_days", 126))
            mode = str(bot.params.get("momentum_mode", "single"))
            horizon_desc = (
                "3m+6m+12m blended return"
                if mode == "blend_3_6_12"
                else (
                    "1m+3m+6m+12m blended return"
                    if mode == "blend_1_3_6_12"
                    else f"{lookback}d return"
                )
            )
            vol_note = (
                " (volatility-balanced)" if bool(bot.params.get("volatility_adjusted", False)) else ""
            )
            sma_n = int(bot.params.get("trend_sma", 200))
            if req.side == "SELL":
                return (
                    f"Dropped out of Top {int(bot.params.get('top_n', 3))} ({horizon_desc}) "
                    f"or fell below {sma_n}-day trend — trimming/exiting via {order_desc}."
                )
            if defensive and req.symbol == defensive:
                return (
                    f"Defensive fallback: not enough assets passed the {sma_n}-day uptrend filter, "
                    f"so unused weight rotates into {defensive} via {order_desc}."
                )
            return (
                f"Ranked in Top {int(bot.params.get('top_n', 3))} by {horizon_desc}{vol_note} and trading "
                f"above its {sma_n}-day trend — buying target weight via {order_desc}."
            )
        if st == "rsi2_pullback_swing":
            if req.side == "BUY":
                return (
                    f"Long-term uptrend intact (> {int(bot.params.get('trend_sma', 200))}-day avg) "
                    f"with a sharp 2-day pullback (RSI2 ≤ {bot.params.get('rsi_buy', 12)}) — buying dip via {order_desc}."
                )
            return (
                f"Bounced back above the {int(bot.params.get('exit_sma', 5))}-day avg (or short/exit trigger hit) "
                f"— closing/shorting via {order_desc}."
            )
        if st == "vwap_rsi_reversion":
            if req.side == "BUY":
                return (
                    f"Price stretched below today's average traded price (VWAP) with oversold RSI "
                    f"— buying mean-reversion bounce via {order_desc}."
                )
            return (
                f"Reverted back to VWAP / overbought extension — selling/shorting via {order_desc}."
            )
        if st == "shock_reversion":
            if req.side == "BUY":
                return f"Sharp intraday selloff stabilized with no fundamental news — buying rebound via {order_desc}."
            return f"Rebound target hit — taking profit via {order_desc}."
        return (
            f"Fast SMA({int(bot.params.get('fast_period', 5))}) vs. Slow SMA({int(bot.params.get('slow_period', 15))}) "
            f"crossover signal — executing via {order_desc}."
        )

    @staticmethod
    def _explain_bot_preview(bot: Any, signal: str, planned: list[BotPlannedOrder]) -> str:
        """Return a plain-English summary of why the bot is placing orders or holding."""
        if planned:
            buys = sum(1 for o in planned if o.side == "BUY")
            sells = sum(1 for o in planned if o.side == "SELL")
            parts: list[str] = []
            if sells:
                parts.append(f"{sells} sell order{'s' if sells > 1 else ''}")
            if buys:
                parts.append(f"{buys} buy order{'s' if buys > 1 else ''}")
            return (
                f"Signal triggered: preparing {' and '.join(parts)} using protective collared limit orders."
            )
        st = bot.strategy_type
        if "Waiting for bars" in signal:
            return f"Still warming up historical bars before it can evaluate ({signal})."
        if st == "rsi2_pullback_swing":
            return (
                f"Waiting for a sharper 2-day dip while in an uptrend (needs RSI2 ≤ {bot.params.get('rsi_buy', 12)} "
                f"above the {int(bot.params.get('trend_sma', 200))}-day avg; currently {signal})."
            )
        if st == "vwap_rsi_reversion":
            return "Price is near its intraday VWAP fair value — waiting for an oversold/overbought stretch."
        if st == "shock_reversion":
            return "No abnormal volatility shock + stabilization bar detected right now."
        if st == "sma_crossover":
            return "No fresh moving-average crossover on the latest bar — holding current stance."
        return "Already aligned with target portfolio weights — no rebalance trades needed."

    def delete_bot(self, bot_id: str) -> None:
        self._strategies.delete_bot(bot_id)
        self._store.delete_bot(bot_id)
        self.log_activity("warning", "strategy", f"Deleted bot '{bot_id}'.")

    def start_strategy(self, name: str, req: StrategyStartRequest) -> StrategyInfo:
        info = self._strategies.start(
            name,
            symbol=req.symbol,
            timeframe=req.timeframe,
            stop_loss_pct=req.stop_loss_pct,
            take_profit_pct=req.take_profit_pct,
            params=req.params,
        )
        self._persist_strategies()
        self.log_activity(
            "info", "strategy", f"Started bot '{name}' on {info.symbol} ({info.timeframe})."
        )
        return info

    def stop_strategy(self, name: str) -> StrategyInfo:
        info = self._strategies.stop(name)
        self._persist_strategies()
        self.log_activity("warning", "strategy", f"Stopped bot '{name}'.")
        return info

    async def get_historical_bars(self, symbol: str, count: int = 60) -> list[PriceBar]:
        return await self.broker.get_historical_bars(symbol, count=count)

    async def step_engine(self) -> None:
        if self._mode == "ibkr" and self._ibkr.should_reconnect:
            reconnected = await self._ibkr.try_reconnect()
            if reconnected:
                self.log_activity("success", "connection", "Reconnected to IBKR automatically.")
                await self.reconcile_broker_state()

        if not self.broker.status().connected:
            return

        if isinstance(self.broker, PaperSimulator):
            self.broker.step_market(self._watchlist)

        await self._strategies.tick_all(
            broker=self.broker,
            submit_order=self.submit_order,
            log_activity=self.log_activity,
            news_checker=self.check_news,
        )
        self._persist_strategies()

        self._tick_count += 1
        if self._tick_count % 20 == 0:
            try:
                acct = await self.broker.get_account_summary()
                self._store.record_equity_snapshot(
                    self._mode, acct.net_liquidation, acct.cash, acct.daily_pnl
                )
            except Exception:
                pass

    async def snapshot(self) -> DashboardSnapshot:
        conn = self.connection_status()
        account: AccountSummary | None = None
        positions: list[Position] = []
        orders: list[OrderRecord] = []
        quotes: list[Quote] = []

        if conn.connected:
            try:
                account = await self.broker.get_account_summary()
                positions = await self.broker.get_positions()
                orders = await self.broker.get_orders()
                for ord_rec in orders:
                    self._store.upsert_order(ord_rec, self._mode)
                quotes = await self.broker.get_quotes(self._watchlist)
            except Exception as exc:
                self.log_activity("error", "broker", f"Refresh error: {exc}")

        return DashboardSnapshot(
            connection=conn,
            market=self.market_status(),
            risk=self.risk_status(),
            account=account,
            positions=positions,
            orders=orders,
            quotes=quotes,
            strategies=self.list_strategies(),
            reconciliation=self._reconciliation,
            activity=list(self._activity),
        )
