"""Domain schemas for broker data, orders, risk guardrails, market status, and strategies."""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

BrokerMode = Literal["simulator", "ibkr"]
OrderSide = Literal["BUY", "SELL"]
OrderType = Literal["MKT", "LMT", "STP"]
TimeInForce = Literal["DAY", "GTC"]
MarketSession = Literal["PRE_MARKET", "RTH", "AFTER_HOURS", "CLOSED"]
Timeframe = Literal["5 mins", "15 mins", "1 hour", "1 day"]
DataSource = Literal["yfinance", "ibkr"]
MomentumMode = Literal["single", "blend_3_6_12", "blend_1_3_6_12"]
StrategyType = Literal[
    "vwap_rsi_reversion",
    "rsi2_pullback_swing",
    "shock_reversion",
    "sma_crossover",
    "dual_momentum",
]

STRATEGY_DEFAULT_TIMEFRAME: dict[StrategyType, Timeframe] = {
    "vwap_rsi_reversion": "5 mins",
    "rsi2_pullback_swing": "1 day",
    "shock_reversion": "5 mins",
    "sma_crossover": "5 mins",
    "dual_momentum": "1 day",
}
OrderStatus = Literal[
    "PendingSubmit",
    "Submitted",
    "Filled",
    "Cancelled",
    "Rejected",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AccountSummary(BaseModel):
    account_id: str
    net_liquidation: float
    cash: float
    buying_power: float
    unrealized_pnl: float
    realized_pnl: float
    daily_pnl: float = 0.0
    currency: str = "USD"


class Position(BaseModel):
    symbol: str
    quantity: float
    avg_cost: float
    market_price: float
    market_value: float
    unrealized_pnl: float


class Quote(BaseModel):
    symbol: str
    bid: float
    ask: float
    last: float
    change_pct: float = 0.0
    volume: int = 0
    timestamp: datetime = Field(default_factory=utc_now)


class PriceBar(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class OrderRequest(BaseModel):
    symbol: str
    side: OrderSide
    quantity: int = Field(gt=0)
    order_type: OrderType = "MKT"
    limit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    stop_loss_price: float | None = Field(default=None, gt=0)
    take_profit_price: float | None = Field(default=None, gt=0)
    tif: TimeInForce = "DAY"
    source: str = "manual"

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned or not cleaned.replace(".", "").isalnum():
            raise ValueError("Symbol must be alphanumeric (e.g. AAPL, BRK.B)")
        return cleaned

    @model_validator(mode="after")
    def validate_prices(self) -> "OrderRequest":
        if self.order_type == "LMT" and self.limit_price is None:
            raise ValueError("limit_price is required for LMT orders")
        if self.order_type == "STP" and self.stop_price is None:
            raise ValueError("stop_price is required for STP orders")
        if self.stop_loss_price is not None and self.take_profit_price is not None:
            if self.side == "BUY" and self.stop_loss_price >= self.take_profit_price:
                raise ValueError(
                    "For BUY brackets, stop_loss_price must be below take_profit_price"
                )
            if self.side == "SELL" and self.stop_loss_price <= self.take_profit_price:
                raise ValueError(
                    "For SELL brackets, stop_loss_price must be above take_profit_price"
                )
        return self


class OrderRecord(BaseModel):
    order_id: int
    symbol: str
    side: OrderSide
    quantity: int
    filled_quantity: int = 0
    order_type: OrderType
    limit_price: float | None = None
    stop_price: float | None = None
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    parent_order_id: int | None = None
    avg_fill_price: float | None = None
    tif: TimeInForce = "DAY"
    status: OrderStatus = "Submitted"
    source: str = "manual"
    message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RiskLimits(BaseModel):
    max_order_qty: int = Field(gt=0)
    max_order_notional: float = Field(gt=0)
    max_position_shares: int = Field(gt=0)
    max_daily_loss: float = Field(gt=0)
    allow_live_trading: bool = False


class RiskLimitsPatch(BaseModel):
    max_order_qty: int | None = Field(default=None, gt=0)
    max_order_notional: float | None = Field(default=None, gt=0)
    max_position_shares: int | None = Field(default=None, gt=0)
    max_daily_loss: float | None = Field(default=None, gt=0)


class RiskStatus(BaseModel):
    trading_halted: bool = False
    halt_reason: str | None = None
    limits: RiskLimits


class StrategyInfo(BaseModel):
    name: str
    strategy_type: StrategyType = "sma_crossover"
    description: str
    running: bool
    symbol: str
    timeframe: Timeframe = "5 mins"
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    last_signal: str | None = None
    last_checked_at: datetime | None = None
    trades_placed: int = 0


class StrategyStartRequest(BaseModel):
    symbol: str | None = None
    timeframe: Timeframe | None = None
    stop_loss_pct: float | None = Field(default=None, gt=0)
    take_profit_pct: float | None = Field(default=None, gt=0)
    params: dict[str, Any] = Field(default_factory=dict)


class BotCreateRequest(BaseModel):
    bot_id: str
    strategy_type: StrategyType
    symbol: str
    timeframe: Timeframe | None = None
    stop_loss_pct: float | None = Field(default=1.5, gt=0)
    take_profit_pct: float | None = Field(default=2.5, gt=0)
    params: dict[str, Any] = Field(default_factory=dict)
    max_allocation_pct: float | None = Field(default=None, gt=0, le=100)
    max_capital: float | None = Field(default=None, gt=0)

    @field_validator("bot_id")
    @classmethod
    def clean_bot_id(cls, value: str) -> str:
        cleaned = value.strip().lower().replace(" ", "-")
        if not cleaned:
            raise ValueError("bot_id cannot be empty")
        return cleaned

    @field_validator("symbol")
    @classmethod
    def clean_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def resolve_default_timeframe(self) -> "BotCreateRequest":
        if self.timeframe is None:
            self.timeframe = STRATEGY_DEFAULT_TIMEFRAME[self.strategy_type]
        return self


class BotBudgetPatch(BaseModel):
    quantity: int | None = Field(default=None, gt=0)
    max_allocation_pct: float | None = Field(default=None, gt=0, le=100)
    max_capital: float | None = Field(default=None, gt=0)
    stop_loss_pct: float | None = Field(default=None, gt=0)
    take_profit_pct: float | None = Field(default=None, gt=0)
    params: dict[str, Any] | None = None


class BotPlannedOrder(BaseModel):
    symbol: str
    side: OrderSide
    quantity: int
    order_type: OrderType
    limit_price: float | None = None
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    estimated_price: float | None = None
    estimated_notional: float | None = None
    risk_status: str = "PASS"
    rationale: str = ""


class BotDryRunPreview(BaseModel):
    bot_id: str
    strategy_type: StrategyType
    symbol: str
    timeframe: Timeframe
    signal: str
    rationale: str = ""
    market_session: str
    market_note: str
    orders: list[BotPlannedOrder] = Field(default_factory=list)


class BarCacheSummary(BaseModel):
    symbol: str
    timeframe: str
    source: str
    bar_count: int
    start_time: str
    end_time: str


class UniverseResponse(BaseModel):
    symbols: list[str]
    cache: list[BarCacheSummary]


class DataSyncRequest(BaseModel):
    symbols: list[str] | None = None
    timeframe: Timeframe = "1 day"
    source: DataSource = "yfinance"
    lookback: str = ""


class BacktestRequest(BaseModel):
    strategy_type: StrategyType
    symbol: str
    timeframe: Timeframe | None = None
    initial_capital: float = Field(default=25_000.0, gt=0)
    quantity: int = Field(default=20, gt=0)
    allocation_pct: float | None = Field(default=None, gt=0, le=100)
    stop_loss_pct: float | None = Field(default=2.0, gt=0)
    take_profit_pct: float | None = Field(default=None, gt=0)
    commission_per_share: float = Field(default=0.005, ge=0)
    slippage_bps: float = Field(default=2.0, ge=0)
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def clean_sym(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def resolve_default_timeframe(self) -> "BacktestRequest":
        if self.timeframe is None:
            self.timeframe = STRATEGY_DEFAULT_TIMEFRAME[self.strategy_type]
        return self


class BacktestTrade(BaseModel):
    entry_time: str
    exit_time: str
    side: OrderSide
    quantity: int
    entry_price: float
    exit_price: float
    pnl: float
    return_pct: float
    exit_reason: str


class EquityPoint(BaseModel):
    time: str
    equity: float
    benchmark_equity: float | None = None
    qqq_equity: float | None = None


class BenchmarkMetrics(BaseModel):
    label: str = "Buy & Hold"
    final_equity: float
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float


class BacktestResult(BaseModel):
    strategy_type: StrategyType
    symbol: str
    timeframe: Timeframe
    bars_tested: int
    initial_capital: float
    final_equity: float
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate_pct: float
    profit_factor: float
    total_trades: int
    exposure_pct: float = 0.0
    alpha_vs_benchmark_pct: float = 0.0
    benchmark: BenchmarkMetrics | None = None
    benchmarks: list[BenchmarkMetrics] = Field(default_factory=list)
    equity_curve: list[EquityPoint]
    trades: list[BacktestTrade]


class PortfolioBotConfig(BaseModel):
    bot_id: str | None = None
    strategy_type: StrategyType
    symbol: str
    timeframe: Timeframe | None = None
    quantity: int = Field(default=20, gt=0)
    allocation_pct: float | None = Field(default=None, gt=0, le=100)
    stop_loss_pct: float | None = Field(default=2.0, gt=0)
    take_profit_pct: float | None = Field(default=None, gt=0)
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def clean_sym(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError("symbol cannot be empty")
        return cleaned

    @model_validator(mode="after")
    def resolve_defaults(self) -> "PortfolioBotConfig":
        if self.timeframe is None:
            self.timeframe = STRATEGY_DEFAULT_TIMEFRAME[self.strategy_type]
        if not self.bot_id or not self.bot_id.strip():
            self.bot_id = f"{self.symbol.lower()}-{self.strategy_type}"
        else:
            self.bot_id = self.bot_id.strip().lower().replace(" ", "-")
        return self


class PortfolioBacktestRequest(BaseModel):
    bots: list[PortfolioBotConfig] = Field(min_length=1)
    initial_capital: float = Field(default=25_000.0, gt=0)
    commission_per_share: float = Field(default=0.005, ge=0)
    slippage_bps: float = Field(default=2.0, ge=0)

    @model_validator(mode="after")
    def validate_unique_symbols(self) -> "PortfolioBacktestRequest":
        seen_symbols: set[str] = set()
        seen_ids: set[str] = set()
        for bot in self.bots:
            if bot.symbol in seen_symbols:
                raise ValueError(
                    f"Duplicate symbol '{bot.symbol}' in portfolio backtest (1 bot per symbol)."
                )
            seen_symbols.add(bot.symbol)
            assert bot.bot_id is not None
            if bot.bot_id in seen_ids:
                raise ValueError(f"Duplicate bot_id '{bot.bot_id}' in portfolio backtest.")
            seen_ids.add(bot.bot_id)
        return self


class PortfolioBacktestTrade(BacktestTrade):
    bot_id: str
    symbol: str


class PortfolioBotResult(BaseModel):
    bot_id: str
    symbol: str
    strategy_type: StrategyType
    timeframe: Timeframe
    bars_tested: int
    total_trades: int
    win_rate_pct: float
    net_pnl: float
    exposure_pct: float
    skipped_for_cash: int = 0


class PortfolioBacktestResult(BaseModel):
    initial_capital: float
    final_equity: float
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate_pct: float
    profit_factor: float
    total_trades: int
    exposure_pct: float
    max_concurrent_positions: int
    skipped_for_cash: int = 0
    alpha_vs_benchmark_pct: float = 0.0
    benchmark: BenchmarkMetrics | None = None
    benchmarks: list[BenchmarkMetrics] = Field(default_factory=list)
    bot_results: list[PortfolioBotResult]
    equity_curve: list[EquityPoint]
    trades: list[PortfolioBacktestTrade]


WalkForwardVerdict = Literal["ROBUST", "DEGRADED", "OVERFIT_WARNING", "NO_TRADES"]


class WalkForwardRequest(BacktestRequest):
    split_ratio: float = Field(default=0.6, ge=0.4, le=0.85)
    auto_tune: bool = True


class WalkForwardResult(BaseModel):
    strategy_type: StrategyType
    symbol: str
    timeframe: Timeframe
    split_ratio: float
    split_time: str
    auto_tuned: bool
    candidates_tested: int
    selected_params: dict[str, Any]
    verdict: WalkForwardVerdict
    verdict_reason: str
    in_sample: BacktestResult
    out_of_sample: BacktestResult


class RotationBacktestRequest(BaseModel):
    symbols: list[str] | None = None
    defensive_symbol: str | None = "TLT"
    top_n: int = Field(default=3, ge=1, le=10)
    lookback_days: int = Field(default=126, ge=20, le=252)
    momentum_mode: MomentumMode = "single"
    etfs_only: bool = False
    volatility_adjusted: bool = False
    trend_sma: int = Field(default=200, ge=20, le=252)
    start_date: str | None = None
    end_date: str | None = None
    initial_capital: float = Field(default=25_000.0, gt=0)
    commission_per_share: float = Field(default=0.005, ge=0)
    slippage_bps: float = Field(default=2.0, ge=0)
    benchmark_symbol: str = "SPY"


class RotationWalkForwardRequest(RotationBacktestRequest):
    split_ratio: float = Field(default=0.6, ge=0.4, le=0.85)
    auto_tune: bool = True


class RotationWalkForwardResult(BaseModel):
    split_ratio: float
    split_time: str
    auto_tuned: bool
    candidates_tested: int
    selected_params: dict[str, Any]
    verdict: WalkForwardVerdict
    verdict_reason: str
    in_sample: PortfolioBacktestResult
    out_of_sample: PortfolioBacktestResult


class DualMomentumAssetScore(BaseModel):
    symbol: str
    trailing_return_pct: float
    last_close: float
    trend_sma: float
    passes_filter: bool
    is_defensive: bool = False
    target_weight_pct: float = 0.0
    estimated_shares: int = 0


class DualMomentumPreview(BaseModel):
    as_of: str
    source: str
    top_n: int
    lookback_days: int
    momentum_mode: MomentumMode = "single"
    etfs_only: bool = False
    volatility_adjusted: bool = False
    trend_sma: int
    defensive_symbol: str | None
    account_equity: float
    summary: str
    targets: dict[str, float]
    rankings: list[DualMomentumAssetScore]


class ConnectionStatus(BaseModel):
    broker_mode: BrokerMode
    connected: bool
    environment: Literal["SIMULATOR", "PAPER", "LIVE"]
    host: str | None = None
    port: int | None = None
    client_id: int | None = None
    auto_reconnect: bool = False
    reconnect_attempts: int = 0
    last_error: str | None = None


class MarketStatus(BaseModel):
    is_rth_open: bool
    session: MarketSession
    et_time: str
    enforce_market_hours: bool
    reason: str


class ReconciliationReport(BaseModel):
    checked_at: datetime = Field(default_factory=utc_now)
    broker_positions: int
    broker_open_orders: int
    db_orders_updated: int
    discrepancies: list[str] = Field(default_factory=list)


class ActivityEvent(BaseModel):
    id: int
    level: Literal["info", "success", "warning", "error"]
    category: str
    message: str
    timestamp: datetime = Field(default_factory=utc_now)


NewsClassification = Literal[
    "CLEAR",
    "MACRO_NOISE",
    "BULLISH_CATALYST",
    "FUNDAMENTAL_RISK",
    "EARNINGS_BLACKOUT",
]
NewsAnalysisSource = Literal["gemini", "heuristic"]


class NewsHeadline(BaseModel):
    title: str
    publisher: str = ""
    published_at: str = ""
    link: str = ""
    summary: str = ""
    catalyst_match: str | None = None


class NewsCheckResult(BaseModel):
    symbol: str
    blocked: bool
    reason: str
    rationale: str | None = None
    classification: NewsClassification = "CLEAR"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    analysis_source: NewsAnalysisSource = "heuristic"
    model: str | None = None
    earnings_date: str | None = None
    headlines: list[NewsHeadline] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=utc_now)


class DashboardSnapshot(BaseModel):
    connection: ConnectionStatus
    market: MarketStatus
    risk: RiskStatus
    account: AccountSummary | None
    positions: list[Position]
    orders: list[OrderRecord]
    quotes: list[Quote]
    strategies: list[StrategyInfo]
    reconciliation: ReconciliationReport | None = None
    activity: list[ActivityEvent]
