export type BrokerMode = "simulator" | "ibkr";
export type OrderSide = "BUY" | "SELL";
export type OrderType = "MKT" | "LMT" | "STP";
export type TimeInForce = "DAY" | "GTC";
export type Timeframe = "5 mins" | "15 mins" | "1 hour" | "1 day";
export type DataSource = "yfinance" | "ibkr";
export type MomentumMode = "single" | "blend_3_6_12" | "blend_1_3_6_12";
export type StrategyType =
  | "vwap_rsi_reversion"
  | "rsi2_pullback_swing"
  | "shock_reversion"
  | "sma_crossover"
  | "dual_momentum";

export interface AccountSummary {
  account_id: string;
  net_liquidation: number;
  cash: number;
  buying_power: number;
  unrealized_pnl: number;
  realized_pnl: number;
  daily_pnl: number;
  currency: string;
  currency_balances?: Record<string, number>;
}

export interface Position {
  symbol: string;
  quantity: number;
  avg_cost: number;
  market_price: number;
  market_value: number;
  unrealized_pnl: number;
}

export interface Quote {
  symbol: string;
  bid: number;
  ask: number;
  last: number;
  change_pct: number;
  volume: number;
  timestamp: string;
}

export interface PriceBar {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface OrderRecord {
  order_id: number;
  symbol: string;
  side: OrderSide;
  quantity: number;
  filled_quantity: number;
  order_type: OrderType;
  limit_price: number | null;
  stop_price: number | null;
  stop_loss_price: number | null;
  take_profit_price: number | null;
  parent_order_id: number | null;
  avg_fill_price: number | null;
  tif: TimeInForce;
  status: string;
  source: string;
  message: string | null;
  created_at: string;
  updated_at: string;
}

export interface OrderRequest {
  symbol: string;
  side: OrderSide;
  quantity: number;
  order_type: OrderType;
  limit_price?: number;
  stop_price?: number;
  stop_loss_price?: number;
  take_profit_price?: number;
  tif: TimeInForce;
}

export interface RiskLimits {
  max_order_qty: number;
  max_order_notional: number;
  max_position_shares: number;
  max_daily_loss: number;
  allow_live_trading: boolean;
}

export interface RiskStatus {
  trading_halted: boolean;
  halt_reason: string | null;
  limits: RiskLimits;
}

export interface StrategyInfo {
  name: string;
  strategy_type: StrategyType;
  description: string;
  running: boolean;
  symbol: string;
  timeframe: Timeframe;
  stop_loss_pct: number | null;
  take_profit_pct: number | null;
  params: Record<string, number | string | boolean>;
  last_signal: string | null;
  last_checked_at: string | null;
  trades_placed: number;
}

export interface BotCreateRequest {
  bot_id: string;
  strategy_type: StrategyType;
  symbol: string;
  timeframe: Timeframe;
  stop_loss_pct: number | null;
  take_profit_pct: number | null;
  params: Record<string, number | string | boolean>;
  max_allocation_pct?: number | null;
  max_capital?: number | null;
}

export interface BotBudgetPatch {
  quantity?: number | null;
  max_allocation_pct?: number | null;
  max_capital?: number | null;
  stop_loss_pct?: number | null;
  take_profit_pct?: number | null;
  params?: Record<string, number | string | boolean>;
}

export interface BotPlannedOrder {
  symbol: string;
  side: OrderSide;
  quantity: number;
  order_type: OrderType;
  limit_price: number | null;
  stop_loss_price: number | null;
  take_profit_price: number | null;
  estimated_price: number | null;
  estimated_notional: number | null;
  risk_status: string;
  rationale: string;
}

export interface BotDryRunPreview {
  bot_id: string;
  strategy_type: StrategyType;
  symbol: string;
  timeframe: Timeframe;
  signal: string;
  rationale: string;
  market_session: string;
  market_note: string;
  orders: BotPlannedOrder[];
}

export interface BarCacheSummary {
  symbol: string;
  timeframe: string;
  source: string;
  bar_count: number;
  start_time: string;
  end_time: string;
}

export interface UniverseResponse {
  symbols: string[];
  cache: BarCacheSummary[];
}

export interface BacktestRequest {
  strategy_type: StrategyType;
  symbol: string;
  timeframe: Timeframe;
  initial_capital: number;
  quantity: number;
  allocation_pct?: number | null;
  stop_loss_pct: number | null;
  take_profit_pct: number | null;
  commission_per_share: number;
  slippage_bps: number;
  params: Record<string, number>;
}

export interface BacktestTrade {
  entry_time: string;
  exit_time: string;
  side: OrderSide;
  quantity: number;
  entry_price: number;
  exit_price: number;
  pnl: number;
  return_pct: number;
  exit_reason: string;
}

export interface EquityPoint {
  time: string;
  equity: number;
  benchmark_equity?: number | null;
  qqq_equity?: number | null;
}

export interface BenchmarkMetrics {
  label: string;
  final_equity: number;
  total_return_pct: number;
  sharpe_ratio: number;
  max_drawdown_pct: number;
}

export interface BacktestResult {
  strategy_type: StrategyType;
  symbol: string;
  timeframe: Timeframe;
  bars_tested: number;
  initial_capital: number;
  final_equity: number;
  total_return_pct: number;
  sharpe_ratio: number;
  max_drawdown_pct: number;
  win_rate_pct: number;
  profit_factor: number;
  total_trades: number;
  exposure_pct?: number;
  alpha_vs_benchmark_pct?: number;
  benchmark?: BenchmarkMetrics | null;
  benchmarks?: BenchmarkMetrics[];
  equity_curve: EquityPoint[];
  trades: BacktestTrade[];
}

export interface PortfolioBotConfig {
  bot_id?: string;
  strategy_type: StrategyType;
  symbol: string;
  timeframe?: Timeframe;
  quantity: number;
  allocation_pct?: number | null;
  stop_loss_pct: number | null;
  take_profit_pct: number | null;
  params?: Record<string, number>;
}

export interface PortfolioBacktestRequest {
  bots: PortfolioBotConfig[];
  initial_capital: number;
  commission_per_share: number;
  slippage_bps: number;
}

export interface PortfolioBacktestTrade extends BacktestTrade {
  bot_id: string;
  symbol: string;
}

export interface PortfolioBotResult {
  bot_id: string;
  symbol: string;
  strategy_type: StrategyType;
  timeframe: Timeframe;
  bars_tested: number;
  total_trades: number;
  win_rate_pct: number;
  net_pnl: number;
  exposure_pct: number;
  skipped_for_cash: number;
}

export interface PortfolioBacktestResult {
  initial_capital: number;
  final_equity: number;
  total_return_pct: number;
  sharpe_ratio: number;
  max_drawdown_pct: number;
  win_rate_pct: number;
  profit_factor: number;
  total_trades: number;
  exposure_pct: number;
  max_concurrent_positions: number;
  skipped_for_cash: number;
  alpha_vs_benchmark_pct: number;
  benchmark: BenchmarkMetrics | null;
  benchmarks?: BenchmarkMetrics[];
  bot_results: PortfolioBotResult[];
  equity_curve: EquityPoint[];
  trades: PortfolioBacktestTrade[];
}

export type WalkForwardVerdict = "ROBUST" | "DEGRADED" | "OVERFIT_WARNING" | "NO_TRADES";

export interface WalkForwardRequest extends BacktestRequest {
  split_ratio: number;
  auto_tune: boolean;
}

export interface WalkForwardResult {
  strategy_type: StrategyType;
  symbol: string;
  timeframe: Timeframe;
  split_ratio: number;
  split_time: string;
  auto_tuned: boolean;
  candidates_tested: number;
  selected_params: Record<string, number>;
  verdict: WalkForwardVerdict;
  verdict_reason: string;
  in_sample: BacktestResult;
  out_of_sample: BacktestResult;
}

export interface RotationBacktestRequest {
  symbols?: string[] | null;
  defensive_symbol?: string | null;
  top_n: number;
  lookback_days: number;
  momentum_mode?: MomentumMode;
  etfs_only?: boolean;
  volatility_adjusted?: boolean;
  trend_sma: number;
  start_date?: string | null;
  end_date?: string | null;
  initial_capital: number;
  commission_per_share: number;
  slippage_bps: number;
  benchmark_symbol: string;
}

export interface RotationWalkForwardRequest extends RotationBacktestRequest {
  split_ratio: number;
  auto_tune: boolean;
}

export interface RotationWalkForwardResult {
  split_ratio: number;
  split_time: string;
  auto_tuned: boolean;
  candidates_tested: number;
  selected_params: Record<string, number>;
  verdict: WalkForwardVerdict;
  verdict_reason: string;
  in_sample: PortfolioBacktestResult;
  out_of_sample: PortfolioBacktestResult;
}

export interface DualMomentumAssetScore {
  symbol: string;
  trailing_return_pct: number;
  last_close: number;
  trend_sma: number;
  passes_filter: boolean;
  is_defensive: boolean;
  target_weight_pct: number;
  estimated_shares: number;
}

export interface DualMomentumPreview {
  as_of: string;
  source: string;
  top_n: number;
  lookback_days: number;
  momentum_mode?: MomentumMode;
  etfs_only?: boolean;
  volatility_adjusted?: boolean;
  trend_sma: number;
  defensive_symbol: string | null;
  account_equity: number;
  summary: string;
  targets: Record<string, number>;
  rankings: DualMomentumAssetScore[];
}

export interface ConnectionStatus {
  broker_mode: BrokerMode;
  connected: boolean;
  environment: "SIMULATOR" | "PAPER" | "LIVE";
  host: string | null;
  port: number | null;
  client_id: number | null;
  auto_reconnect: boolean;
  reconnect_attempts: number;
  last_error: string | null;
}

export interface MarketStatus {
  is_rth_open: boolean;
  session: "PRE_MARKET" | "RTH" | "AFTER_HOURS" | "CLOSED";
  et_time: string;
  enforce_market_hours: boolean;
  reason: string;
}

export interface ReconciliationReport {
  checked_at: string;
  broker_positions: number;
  broker_open_orders: number;
  db_orders_updated: number;
  discrepancies: string[];
}

export interface ActivityEvent {
  id: number;
  level: "info" | "success" | "warning" | "error";
  category: string;
  message: string;
  timestamp: string;
}

export type NewsClassification =
  | "CLEAR"
  | "MACRO_NOISE"
  | "BULLISH_CATALYST"
  | "FUNDAMENTAL_RISK"
  | "EARNINGS_BLACKOUT";

export interface NewsHeadline {
  title: string;
  publisher: string;
  published_at: string;
  link: string;
  summary?: string;
  catalyst_match: string | null;
}

export interface NewsCheckResult {
  symbol: string;
  blocked: boolean;
  reason: string;
  rationale?: string | null;
  classification?: NewsClassification;
  confidence?: number | null;
  analysis_source?: "gemini" | "heuristic";
  model?: string | null;
  earnings_date: string | null;
  headlines: NewsHeadline[];
  checked_at: string;
}

export interface DashboardSnapshot {
  connection: ConnectionStatus;
  market: MarketStatus;
  risk: RiskStatus;
  account: AccountSummary | null;
  positions: Position[];
  orders: OrderRecord[];
  quotes: Quote[];
  strategies: StrategyInfo[];
  reconciliation: ReconciliationReport | null;
  activity: ActivityEvent[];
}

export interface CurrencyConversionRequest {
  from_currency: string;
  to_currency: string;
  amount: number;
  amount_currency: string;
  order_type: "MKT" | "LMT";
  limit_price?: number;
  exchange: "IDEALPRO" | "FXCONV";
}

export interface CurrencyConversionResult {
  success: boolean;
  order_id: number | null;
  from_currency: string;
  to_currency: string;
  from_amount: number;
  to_amount: number;
  rate: number;
  exchange: string;
  status: string;
  message: string;
  created_at: string;
}

export interface FxRateResponse {
  pair: string;
  base_currency: string;
  quote_currency: string;
  rate: number;
  bid: number;
  ask: number;
  inverse_rate: number;
  updated_at: string;
}

