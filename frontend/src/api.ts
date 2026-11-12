import type {
  BacktestRequest,
  BacktestResult,
  BotBudgetPatch,
  BotCreateRequest,
  BotDryRunPreview,
  BrokerMode,
  ConnectionStatus,
  DashboardSnapshot,
  DataSource,
  DualMomentumPreview,
  NewsCheckResult,
  OrderRecord,
  OrderRequest,
  PortfolioBacktestRequest,
  PortfolioBacktestResult,
  PriceBar,
  ReconciliationReport,
  RiskLimits,
  RiskStatus,
  RotationBacktestRequest,
  RotationWalkForwardRequest,
  RotationWalkForwardResult,
  StrategyInfo,
  Timeframe,
  UniverseResponse,
  WalkForwardRequest,
  WalkForwardResult,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      const data = (await res.json()) as { detail?: string };
      if (data.detail) msg = data.detail;
    } catch {
      // ignore non-json body
    }
    throw new Error(msg);
  }
  return (await res.json()) as T;
}

export const api = {
  getSnapshot: () => request<DashboardSnapshot>("/api/snapshot"),
  getHistory: (symbol: string, count = 60) =>
    request<PriceBar[]>(`/api/market/history/${encodeURIComponent(symbol)}?count=${count}`),
  getNews: (symbol: string, refresh = false) =>
    request<NewsCheckResult>(`/api/market/news/${encodeURIComponent(symbol)}?refresh=${refresh}`),
  connect: () => request<ConnectionStatus>("/api/connection/connect", { method: "POST" }),
  disconnect: () => request<ConnectionStatus>("/api/connection/disconnect", { method: "POST" }),
  switchMode: (mode: BrokerMode, host?: string, port?: number, client_id?: number) =>
    request<ConnectionStatus>("/api/connection/mode", {
      method: "POST",
      body: JSON.stringify({ mode, host, port, client_id }),
    }),
  reconcile: () =>
    request<ReconciliationReport>("/api/connection/reconcile", { method: "POST" }),
  placeOrder: (order: OrderRequest) =>
    request<OrderRecord>("/api/orders", {
      method: "POST",
      body: JSON.stringify(order),
    }),
  cancelOrder: (orderId: number) =>
    request<OrderRecord>(`/api/orders/${orderId}`, { method: "DELETE" }),
  addWatchlist: (symbol: string) =>
    request<string[]>("/api/market/watchlist", {
      method: "POST",
      body: JSON.stringify({ symbol }),
    }),
  removeWatchlist: (symbol: string) =>
    request<string[]>(`/api/market/watchlist/${encodeURIComponent(symbol)}`, {
      method: "DELETE",
    }),
  setKillSwitch: (halted: boolean, cancel_open_orders = true, reason?: string) =>
    request<RiskStatus>("/api/risk/kill-switch", {
      method: "POST",
      body: JSON.stringify({ halted, cancel_open_orders, reason }),
    }),
  updateRiskLimits: (patch: Partial<Omit<RiskLimits, "allow_live_trading">>) =>
    request<RiskStatus>("/api/risk/limits", {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  startStrategy: (name: string, symbol: string, params: Record<string, number>) =>
    request<StrategyInfo>(`/api/strategies/${encodeURIComponent(name)}/start`, {
      method: "POST",
      body: JSON.stringify({ symbol, params }),
    }),
  stopStrategy: (name: string) =>
    request<StrategyInfo>(`/api/strategies/${encodeURIComponent(name)}/stop`, {
      method: "POST",
    }),
  createBot: (body: BotCreateRequest) =>
    request<StrategyInfo>("/api/bots", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  startBot: (botId: string) =>
    request<StrategyInfo>(`/api/bots/${encodeURIComponent(botId)}/start`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  stopBot: (botId: string) =>
    request<StrategyInfo>(`/api/bots/${encodeURIComponent(botId)}/stop`, {
      method: "POST",
    }),
  updateBotBudget: (botId: string, body: BotBudgetPatch) =>
    request<StrategyInfo>(`/api/bots/${encodeURIComponent(botId)}/budget`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  previewBot: (botId: string) =>
    request<BotDryRunPreview>(`/api/bots/${encodeURIComponent(botId)}/preview`),
  deleteBot: (botId: string) =>
    request<{ deleted: string }>(`/api/bots/${encodeURIComponent(botId)}`, {
      method: "DELETE",
    }),
  getUniverse: () => request<UniverseResponse>("/api/universe"),
  syncData: (
    timeframe: Timeframe,
    source: DataSource,
    symbols?: string[],
    lookback?: string
  ) =>
    request<{ synced: Record<string, number> }>("/api/backtest/sync", {
      method: "POST",
      body: JSON.stringify({ timeframe, source, symbols, lookback: lookback || "" }),
    }),
  runBacktest: (body: BacktestRequest) =>
    request<BacktestResult>("/api/backtest/run", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  runPortfolioBacktest: (body: PortfolioBacktestRequest) =>
    request<PortfolioBacktestResult>("/api/backtest/portfolio", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  runWalkForward: (body: WalkForwardRequest) =>
    request<WalkForwardResult>("/api/backtest/walk-forward", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  runRotationBacktest: (body: RotationBacktestRequest) =>
    request<PortfolioBacktestResult>("/api/backtest/rotation", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  runRotationWalkForward: (body: RotationWalkForwardRequest) =>
    request<RotationWalkForwardResult>("/api/backtest/rotation/walk-forward", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  previewDualMomentum: () =>
    request<DualMomentumPreview>("/api/strategies/dual-momentum/preview"),
};

