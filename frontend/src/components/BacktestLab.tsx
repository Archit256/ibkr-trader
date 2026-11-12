import { useEffect, useMemo, useState } from "react";
import { Compass, Database, GitBranch, Layers, PlayCircle, Plus, Trash2 } from "lucide-react";
import { api } from "../api";
import type {
  BacktestResult,
  BarCacheSummary,
  BenchmarkMetrics,
  DataSource,
  EquityPoint,
  PortfolioBacktestResult,
  PortfolioBacktestTrade,
  PortfolioBotConfig,
  RotationWalkForwardResult,
  StrategyType,
  Timeframe,
  WalkForwardResult,
} from "../types";

interface BacktestLabProps {
  universe: string[];
  ibkrConnected: boolean;
}

interface ParamFieldDef {
  key: string;
  label: string;
  defaultValue: number;
  step?: string;
  min?: number;
}

const STRATEGY_PARAM_FIELDS: Record<StrategyType, ParamFieldDef[]> = {
  rsi2_pullback_swing: [
    { key: "rsi_period", label: "RSI Period", defaultValue: 2, min: 2 },
    { key: "rsi_buy", label: "RSI Buy <=", defaultValue: 12, step: "1", min: 1 },
    { key: "trend_sma", label: "Trend SMA", defaultValue: 200, min: 20 },
    { key: "exit_sma", label: "Exit SMA", defaultValue: 5, min: 2 },
  ],
  vwap_rsi_reversion: [
    { key: "rsi_period", label: "RSI Period", defaultValue: 14, min: 2 },
    { key: "rsi_oversold", label: "RSI Oversold <=", defaultValue: 30, step: "1", min: 1 },
    { key: "rsi_exit", label: "RSI Exit >=", defaultValue: 55, step: "1", min: 10 },
    { key: "vwap_dev_pct", label: "VWAP Dip (%)", defaultValue: 0.45, step: "0.05", min: 0.05 },
  ],
  shock_reversion: [
    { key: "atr_period", label: "ATR Period", defaultValue: 14, min: 5 },
    { key: "shock_atr_mult", label: "Shock ATR Mult", defaultValue: 1.8, step: "0.1", min: 0.5 },
    { key: "vol_mult", label: "Volume Mult", defaultValue: 2.0, step: "0.1", min: 1.0 },
    { key: "bounce_atr_mult", label: "Bounce ATR Mult", defaultValue: 1.5, step: "0.1", min: 0.5 },
  ],
  sma_crossover: [
    { key: "fast_period", label: "Fast Period (bars)", defaultValue: 5, min: 2 },
    { key: "slow_period", label: "Slow Period (bars)", defaultValue: 15, min: 3 },
  ],
  dual_momentum: [
    { key: "top_n", label: "Top N Assets", defaultValue: 3, min: 1 },
    { key: "lookback_days", label: "Lookback (days)", defaultValue: 126, min: 20 },
    { key: "trend_sma", label: "Trend SMA", defaultValue: 200, min: 20 },
  ],
};

function defaultParamStrings(st: StrategyType): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of STRATEGY_PARAM_FIELDS[st]) {
    out[f.key] = String(f.defaultValue);
  }
  return out;
}

const STRATEGY_PRESETS: Record<
  StrategyType,
  { timeframe: Timeframe; stopLossPct: string; takeProfitPct: string }
> = {
  rsi2_pullback_swing: { timeframe: "1 day", stopLossPct: "3.0", takeProfitPct: "" },
  vwap_rsi_reversion: { timeframe: "5 mins", stopLossPct: "1.0", takeProfitPct: "1.2" },
  shock_reversion: { timeframe: "5 mins", stopLossPct: "1.5", takeProfitPct: "2.0" },
  sma_crossover: { timeframe: "5 mins", stopLossPct: "1.5", takeProfitPct: "3.0" },
  dual_momentum: { timeframe: "1 day", stopLossPct: "", takeProfitPct: "" },
};

const SWING_FLEET_PRESET: PortfolioBotConfig[] = [
  { bot_id: "spy-rsi2", strategy_type: "rsi2_pullback_swing", symbol: "SPY", timeframe: "1 day", quantity: 15, allocation_pct: 25, stop_loss_pct: 3.0, take_profit_pct: null },
  { bot_id: "qqq-rsi2", strategy_type: "rsi2_pullback_swing", symbol: "QQQ", timeframe: "1 day", quantity: 15, allocation_pct: 25, stop_loss_pct: 3.0, take_profit_pct: null },
  { bot_id: "iwm-rsi2", strategy_type: "rsi2_pullback_swing", symbol: "IWM", timeframe: "1 day", quantity: 25, allocation_pct: 25, stop_loss_pct: 3.0, take_profit_pct: null },
  { bot_id: "tlt-rsi2", strategy_type: "rsi2_pullback_swing", symbol: "TLT", timeframe: "1 day", quantity: 50, allocation_pct: 25, stop_loss_pct: 3.0, take_profit_pct: null },
  { bot_id: "gld-rsi2", strategy_type: "rsi2_pullback_swing", symbol: "GLD", timeframe: "1 day", quantity: 20, allocation_pct: 25, stop_loss_pct: 3.0, take_profit_pct: null },
];

const INTRADAY_FLEET_PRESET: PortfolioBotConfig[] = [
  { bot_id: "spy-vwap", strategy_type: "vwap_rsi_reversion", symbol: "SPY", timeframe: "5 mins", quantity: 15, allocation_pct: 25, stop_loss_pct: 1.0, take_profit_pct: 1.2 },
  { bot_id: "qqq-vwap", strategy_type: "vwap_rsi_reversion", symbol: "QQQ", timeframe: "5 mins", quantity: 15, allocation_pct: 25, stop_loss_pct: 1.0, take_profit_pct: 1.2 },
  { bot_id: "nvda-shock", strategy_type: "shock_reversion", symbol: "NVDA", timeframe: "5 mins", quantity: 25, allocation_pct: 25, stop_loss_pct: 1.5, take_profit_pct: 2.0 },
  { bot_id: "amd-shock", strategy_type: "shock_reversion", symbol: "AMD", timeframe: "5 mins", quantity: 25, allocation_pct: 25, stop_loss_pct: 1.5, take_profit_pct: 2.0 },
  { bot_id: "aapl-sma", strategy_type: "sma_crossover", symbol: "AAPL", timeframe: "5 mins", quantity: 20, allocation_pct: 25, stop_loss_pct: 1.5, take_profit_pct: 3.0 },
];

function buildEquitySvg(curve: EquityPoint[]) {
  if (curve.length < 2) return null;
  const w = 740;
  const h = 210;
  const pad = 16;
  const stratVals = curve.map((p) => p.equity);
  const benchVals = curve
    .map((p) => p.benchmark_equity)
    .filter((v): v is number => typeof v === "number");
  const qqqVals = curve
    .map((p) => p.qqq_equity)
    .filter((v): v is number => typeof v === "number");
  const allVals = [...stratVals, ...benchVals, ...qqqVals];
  const minE = Math.min(...allVals);
  const maxE = Math.max(...allVals);
  const span = Math.max(1, maxE - minE);
  const toPoints = (getter: (p: EquityPoint) => number) =>
    curve
      .map((p, idx) => {
        const x = pad + (idx / (curve.length - 1)) * (w - pad * 2);
        const y = pad + ((maxE - getter(p)) / span) * (h - pad * 2);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");

  const pts = toPoints((p) => p.equity);
  const benchPts =
    benchVals.length === curve.length
      ? toPoints((p) => p.benchmark_equity ?? p.equity)
      : null;
  const qqqPts =
    qqqVals.length === curve.length
      ? toPoints((p) => p.qqq_equity ?? p.equity)
      : null;
  return { w, h, pts, benchPts, qqqPts, minE, maxE };
}

export function BacktestLab({ universe, ibkrConnected }: BacktestLabProps) {
  const [mode, setMode] = useState<"single" | "portfolio" | "walk_forward" | "rotation">("single");

  const [strategyType, setStrategyType] = useState<StrategyType>("rsi2_pullback_swing");
  const [symbol, setSymbol] = useState(universe[0] || "SPY");
  const [timeframe, setTimeframe] = useState<Timeframe>("1 day");
  const [source, setSource] = useState<DataSource>("yfinance");
  const [initialCapital, setInitialCapital] = useState("25000");
  const [sizingMode, setSizingMode] = useState<"pct_equity" | "fixed_shares">("pct_equity");
  const [allocationPct, setAllocationPct] = useState("100");
  const [quantity, setQuantity] = useState("25");
  const [stopLossPct, setStopLossPct] = useState("3.0");
  const [takeProfitPct, setTakeProfitPct] = useState("");
  const [slippageBps, setSlippageBps] = useState("2.0");
  const [strategyParams, setStrategyParams] = useState<Record<string, string>>(
    () => defaultParamStrings("rsi2_pullback_swing")
  );
  const [splitPct, setSplitPct] = useState("60");
  const [autoTune, setAutoTune] = useState(true);

  // Dual-Momentum Rotation state
  const [rotTopN, setRotTopN] = useState("3");
  const [rotMomentumMode, setRotMomentumMode] = useState<"single" | "blend_3_6_12" | "blend_1_3_6_12">("blend_3_6_12");
  const [rotEtfsOnly, setRotEtfsOnly] = useState(false);
  const [rotVolAdjusted, setRotVolAdjusted] = useState(true);
  const [rotLookback, setRotLookback] = useState("126");
  const [rotTrendSma, setRotTrendSma] = useState("200");
  const [rotDefensive, setRotDefensive] = useState("SHY");
  const [rotStartDate, setRotStartDate] = useState("");
  const [rotEndDate, setRotEndDate] = useState("");
  const [rotWalkForward, setRotWalkForward] = useState(false);

  // Multi-bot portfolio builder state
  const [fleetSizingMode, setFleetSizingMode] = useState<"pct_equity" | "fixed_shares">(
    "pct_equity"
  );
  const [fleetBots, setFleetBots] = useState<PortfolioBotConfig[]>(SWING_FLEET_PRESET);
  const [newBotStrat, setNewBotStrat] = useState<StrategyType>("vwap_rsi_reversion");
  const [newBotSym, setNewBotSym] = useState("NVDA");
  const [newBotTf, setNewBotTf] = useState<Timeframe>("5 mins");
  const [newBotAlloc, setNewBotAlloc] = useState("25");
  const [newBotQty, setNewBotQty] = useState("15");
  const [newBotSl, setNewBotSl] = useState("1.0");
  const [newBotTp, setNewBotTp] = useState("1.2");

  const [cache, setCache] = useState<BarCacheSummary[]>([]);
  const [syncing, setSyncing] = useState(false);
  const [running, setRunning] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [portfolioResult, setPortfolioResult] = useState<PortfolioBacktestResult | null>(null);
  const [wfResult, setWfResult] = useState<WalkForwardResult | null>(null);
  const [rotWfResult, setRotWfResult] = useState<RotationWalkForwardResult | null>(null);
  const [wfTradesTab, setWfTradesTab] = useState<"oos" | "is">("oos");

  const availableSymbols = useMemo(() => {
    const used = new Set(fleetBots.map((b) => b.symbol));
    return universe.filter((sym) => !used.has(sym));
  }, [universe, fleetBots]);

  useEffect(() => {
    if (availableSymbols.length > 0 && !availableSymbols.includes(newBotSym)) {
      setNewBotSym(availableSymbols[0]);
    }
  }, [availableSymbols, newBotSym]);

  async function refreshUniverse() {
    try {
      const data = await api.getUniverse();
      setCache(data.cache);
    } catch {
      // ignore initial error
    }
  }

  useEffect(() => {
    void refreshUniverse();
  }, []);

  async function handleSyncUniverse() {
    setSyncing(true);
    setError(null);
    setStatusMsg(null);
    try {
      const res = await api.syncData(timeframe, source, universe);
      const total = Object.values(res.synced).reduce((a, b) => a + b, 0);
      setStatusMsg(
        `Synced ${total.toLocaleString()} ${timeframe} bars from ${source} across ${Object.keys(res.synced).length} symbols.`
      );
      await refreshUniverse();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Data sync failed");
    } finally {
      setSyncing(false);
    }
  }

  async function handleRunBacktest(e: React.FormEvent) {
    e.preventDefault();
    setRunning(true);
    setError(null);
    try {
      if (mode === "single") {
        const parsedParams: Record<string, number> = {};
        for (const f of STRATEGY_PARAM_FIELDS[strategyType]) {
          const val = Number(strategyParams[f.key]);
          if (Number.isFinite(val) && val > 0) {
            parsedParams[f.key] = val;
          }
        }
        const alloc =
          sizingMode === "pct_equity"
            ? Math.min(100, Math.max(1, Number(allocationPct) || 100))
            : null;
        const res = await api.runBacktest({
          strategy_type: strategyType,
          symbol,
          timeframe,
          initial_capital: Number(initialCapital) || 25000,
          quantity: Number(quantity) || 25,
          allocation_pct: alloc,
          stop_loss_pct: stopLossPct ? Number(stopLossPct) : null,
          take_profit_pct: takeProfitPct ? Number(takeProfitPct) : null,
          commission_per_share: 0.005,
          slippage_bps: Number(slippageBps) || 2,
          params: parsedParams,
        });
        setResult(res);
      } else if (mode === "walk_forward") {
        const parsedParams: Record<string, number> = {};
        for (const f of STRATEGY_PARAM_FIELDS[strategyType]) {
          const val = Number(strategyParams[f.key]);
          if (Number.isFinite(val) && val > 0) {
            parsedParams[f.key] = val;
          }
        }
        const alloc =
          sizingMode === "pct_equity"
            ? Math.min(100, Math.max(1, Number(allocationPct) || 100))
            : null;
        const ratio = Math.min(0.85, Math.max(0.4, (Number(splitPct) || 60) / 100));
        const res = await api.runWalkForward({
          strategy_type: strategyType,
          symbol,
          timeframe,
          initial_capital: Number(initialCapital) || 25000,
          quantity: Number(quantity) || 25,
          allocation_pct: alloc,
          stop_loss_pct: stopLossPct ? Number(stopLossPct) : null,
          take_profit_pct: takeProfitPct ? Number(takeProfitPct) : null,
          commission_per_share: 0.005,
          slippage_bps: Number(slippageBps) || 2,
          params: parsedParams,
          split_ratio: ratio,
          auto_tune: autoTune,
        });
        setWfResult(res);
      } else if (mode === "rotation") {
        const commonRot = {
          symbols: universe,
          defensive_symbol: rotDefensive || null,
          top_n: Math.max(1, Number(rotTopN) || 3),
          lookback_days: Math.max(20, Number(rotLookback) || 126),
          momentum_mode: rotMomentumMode,
          etfs_only: rotEtfsOnly,
          volatility_adjusted: rotVolAdjusted,
          trend_sma: Math.max(20, Number(rotTrendSma) || 200),
          start_date: rotStartDate || null,
          end_date: rotEndDate || null,
          initial_capital: Number(initialCapital) || 25000,
          commission_per_share: 0.005,
          slippage_bps: Number(slippageBps) || 2,
          benchmark_symbol: "SPY",
        };
        if (rotWalkForward) {
          const ratio = Math.min(0.85, Math.max(0.4, (Number(splitPct) || 60) / 100));
          const res = await api.runRotationWalkForward({
            ...commonRot,
            split_ratio: ratio,
            auto_tune: autoTune,
          });
          setRotWfResult(res);
          setPortfolioResult(null);
        } else {
          const res = await api.runRotationBacktest(commonRot);
          setPortfolioResult(res);
          setRotWfResult(null);
        }
      } else {
        if (fleetBots.length === 0) {
          throw new Error("Add at least 1 bot to run a portfolio backtest.");
        }
        const configuredBots = fleetBots.map((b) => ({
          ...b,
          allocation_pct:
            fleetSizingMode === "pct_equity"
              ? Math.min(100, Math.max(1, b.allocation_pct ?? 25))
              : null,
        }));
        const res = await api.runPortfolioBacktest({
          bots: configuredBots,
          initial_capital: Number(initialCapital) || 25000,
          commission_per_share: 0.005,
          slippage_bps: Number(slippageBps) || 2,
        });
        setPortfolioResult(res);
      }
      await refreshUniverse();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Backtest failed");
    } finally {
      setRunning(false);
    }
  }

  function handleAddBotToFleet() {
    if (!newBotSym || fleetBots.some((b) => b.symbol === newBotSym)) return;
    setFleetBots((prev) => [
      ...prev,
      {
        bot_id: `${newBotSym.toLowerCase()}-${newBotStrat.split("_")[0]}`,
        strategy_type: newBotStrat,
        symbol: newBotSym,
        timeframe: newBotTf,
        quantity: Math.max(1, Number(newBotQty) || 15),
        allocation_pct: Math.min(100, Math.max(1, Number(newBotAlloc) || 25)),
        stop_loss_pct: newBotSl ? Number(newBotSl) : null,
        take_profit_pct: newBotTp ? Number(newBotTp) : null,
      },
    ]);
  }

  const activeSummary: {
    title: string;
    barsTestedLabel: string;
    initialCapital: number;
    finalEquity: number;
    totalReturnPct: number;
    sharpeRatio: number;
    maxDrawdownPct: number;
    winRatePct: number;
    profitFactor: number;
    totalTrades: number;
    exposurePct?: number;
    alphaPct?: number;
    benchmark: BenchmarkMetrics | null | undefined;
    benchmarks: BenchmarkMetrics[];
    curve: EquityPoint[];
    trades: (PortfolioBacktestTrade | (BacktestResult["trades"][number] & { bot_id?: string; symbol?: string }))[];
  } | null = useMemo(() => {
    if (mode === "single" && result) {
      return {
        title: `${result.symbol} (${result.strategy_type})`,
        barsTestedLabel: `${result.bars_tested} bars tested`,
        initialCapital: result.initial_capital,
        finalEquity: result.final_equity,
        totalReturnPct: result.total_return_pct,
        sharpeRatio: result.sharpe_ratio,
        maxDrawdownPct: result.max_drawdown_pct,
        winRatePct: result.win_rate_pct,
        profitFactor: result.profit_factor,
        totalTrades: result.total_trades,
        exposurePct: result.exposure_pct,
        alphaPct: result.alpha_vs_benchmark_pct,
        benchmark: result.benchmark,
        benchmarks:
          result.benchmarks && result.benchmarks.length > 0
            ? result.benchmarks
            : result.benchmark
              ? [result.benchmark]
              : [],
        curve: result.equity_curve,
        trades: result.trades,
      };
    }
    if ((mode === "portfolio" || (mode === "rotation" && !rotWfResult)) && portfolioResult) {
      return {
        title:
          mode === "rotation"
            ? `Dual-Momentum Rotation (Top ${rotTopN}, ${rotLookback}d)`
            : `Multi-Bot Fleet (${portfolioResult.bot_results.length} bots)`,
        barsTestedLabel: `Max ${portfolioResult.max_concurrent_positions} concurrent · ${portfolioResult.skipped_for_cash} cash-skipped`,
        initialCapital: portfolioResult.initial_capital,
        finalEquity: portfolioResult.final_equity,
        totalReturnPct: portfolioResult.total_return_pct,
        sharpeRatio: portfolioResult.sharpe_ratio,
        maxDrawdownPct: portfolioResult.max_drawdown_pct,
        winRatePct: portfolioResult.win_rate_pct,
        profitFactor: portfolioResult.profit_factor,
        totalTrades: portfolioResult.total_trades,
        exposurePct: portfolioResult.exposure_pct,
        alphaPct: portfolioResult.alpha_vs_benchmark_pct,
        benchmark: portfolioResult.benchmark,
        benchmarks:
          portfolioResult.benchmarks && portfolioResult.benchmarks.length > 0
            ? portfolioResult.benchmarks
            : portfolioResult.benchmark
              ? [portfolioResult.benchmark]
              : [],
        curve: portfolioResult.equity_curve,
        trades: portfolioResult.trades,
      };
    }
    return null;
  }, [mode, result, portfolioResult, rotWfResult, rotTopN, rotLookback]);

  const equitySvg = useMemo(
    () => (activeSummary ? buildEquitySvg(activeSummary.curve) : null),
    [activeSummary]
  );

  const activeWf = useMemo(() => {
    if (mode === "walk_forward" && wfResult) {
      return {
        title: `${wfResult.symbol} (${wfResult.strategy_type})`,
        splitRatio: wfResult.split_ratio,
        splitTime: wfResult.split_time,
        candidatesTested: wfResult.candidates_tested,
        selectedParams: wfResult.selected_params,
        verdict: wfResult.verdict,
        verdictReason: wfResult.verdict_reason,
        inSample: wfResult.in_sample,
        outOfSample: wfResult.out_of_sample,
      };
    }
    if (mode === "rotation" && rotWfResult) {
      return {
        title: `Dual-Momentum Rotation (${universe.length} symbols)`,
        splitRatio: rotWfResult.split_ratio,
        splitTime: rotWfResult.split_time,
        candidatesTested: rotWfResult.candidates_tested,
        selectedParams: rotWfResult.selected_params,
        verdict: rotWfResult.verdict,
        verdictReason: rotWfResult.verdict_reason,
        inSample: rotWfResult.in_sample,
        outOfSample: rotWfResult.out_of_sample,
      };
    }
    return null;
  }, [mode, wfResult, rotWfResult, universe.length]);

  const wfIsSvg = useMemo(
    () => (activeWf ? buildEquitySvg(activeWf.inSample.equity_curve) : null),
    [activeWf]
  );

  const wfOosSvg = useMemo(
    () => (activeWf ? buildEquitySvg(activeWf.outOfSample.equity_curve) : null),
    [activeWf]
  );

  return (
    <div className="backtest-layout">
      <div className="card">
        <div className="card-header">
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <h3>Historical Backtest Lab</h3>
            <div className="nav-tabs">
              <button
                type="button"
                className={`nav-tab ${mode === "single" ? "active" : ""}`}
                onClick={() => {
                  setMode("single");
                  setError(null);
                }}
              >
                Single Bot
              </button>
              <button
                type="button"
                className={`nav-tab ${mode === "portfolio" ? "active" : ""}`}
                onClick={() => {
                  setMode("portfolio");
                  setError(null);
                }}
              >
                <Layers size={13} />
                Multi-Bot Fleet
              </button>
              <button
                type="button"
                className={`nav-tab ${mode === "walk_forward" ? "active" : ""}`}
                onClick={() => {
                  setMode("walk_forward");
                  setError(null);
                }}
              >
                <GitBranch size={13} />
                Walk-Forward
              </button>
              <button
                type="button"
                className={`nav-tab ${mode === "rotation" ? "active" : ""}`}
                onClick={() => {
                  setMode("rotation");
                  setError(null);
                }}
              >
                <Compass size={13} />
                Dual-Momentum Rotation
              </button>
            </div>
          </div>

          <button
            type="button"
            className="btn-outline"
            disabled={syncing || (source === "ibkr" && !ibkrConnected)}
            onClick={handleSyncUniverse}
          >
            <Database size={14} />
            {syncing ? "Syncing Universe..." : `Sync All ${universe.length} Symbols (${timeframe})`}
          </button>
        </div>

        <form className="order-form" onSubmit={handleRunBacktest}>
          {mode === "rotation" ? (
            <>
              <div className="muted-sm">
                Monthly cross-sectional momentum across your {universe.length}-symbol universe: holds the Top N assets by trailing return that are also positive and above their 200-SMA; weak slots rotate into the defensive asset (if in uptrend) or cash.
              </div>
              <div className="form-grid-4">
                <label className="field">
                  <span>Top N Holdings</span>
                  <input
                    type="number"
                    min={1}
                    max={10}
                    value={rotTopN}
                    onChange={(e) => setRotTopN(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span>Momentum Horizon</span>
                  <select
                    value={rotMomentumMode}
                    onChange={(e) =>
                      setRotMomentumMode(
                        e.target.value as "single" | "blend_3_6_12" | "blend_1_3_6_12"
                      )
                    }
                  >
                    <option value="blend_3_6_12">3m + 6m + 12m Blend (63/126/252d)</option>
                    <option value="blend_1_3_6_12">1m + 3m + 6m + 12m Blend</option>
                    <option value="single">Single Lookback (days)</option>
                  </select>
                </label>
                {rotMomentumMode === "single" && (
                  <label className="field">
                    <span>Momentum Lookback (days)</span>
                    <input
                      type="number"
                      min={20}
                      max={252}
                      value={rotLookback}
                      onChange={(e) => setRotLookback(e.target.value)}
                    />
                  </label>
                )}
                <label className="field">
                  <span>Trend Filter SMA (days)</span>
                  <input
                    type="number"
                    min={20}
                    max={252}
                    value={rotTrendSma}
                    onChange={(e) => setRotTrendSma(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span>Defensive Slot Fallback</span>
                  <select
                    value={rotDefensive}
                    onChange={(e) => setRotDefensive(e.target.value)}
                  >
                    <option value="SHY">SHY (1–3Y Treasuries) or Cash</option>
                    <option value="SGOV">SGOV (0–3M T-Bills, post-2020) or Cash</option>
                    <option value="TLT">TLT (20Y+ Long Treasuries) or Cash</option>
                    <option value="GLD">GLD (Gold) or Cash</option>
                    <option value="">Cash Only (100% safe)</option>
                  </select>
                </label>
              </div>
              <div className="form-grid-2">
                <label className="field">
                  <span>Universe Scope</span>
                  <select
                    value={rotEtfsOnly ? "etfs" : "mixed"}
                    onChange={(e) => setRotEtfsOnly(e.target.value === "etfs")}
                  >
                    <option value="mixed">Mixed (12 US ETFs + 6 Mega-Cap Tech Stocks)</option>
                    <option value="etfs">ETFs Only (12 US Sector &amp; Asset ETFs)</option>
                  </select>
                </label>
                <label
                  className="field"
                  style={{ flexDirection: "row", alignItems: "center", gap: 8, paddingTop: 20 }}
                >
                  <input
                    type="checkbox"
                    checked={rotVolAdjusted}
                    onChange={(e) => setRotVolAdjusted(e.target.checked)}
                    style={{ width: "auto" }}
                  />
                  <span>
                    Volatility Balance (Rank by Return/Vol &amp; Inverse-Volatility Sizing)
                  </span>
                </label>
              </div>
              <div className="form-grid-4">
                <label className="field">
                  <span>Start Date (optional)</span>
                  <input
                    type="date"
                    value={rotStartDate}
                    onChange={(e) => setRotStartDate(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span>End Date (optional)</span>
                  <input
                    type="date"
                    value={rotEndDate}
                    onChange={(e) => setRotEndDate(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span>Initial Capital ($)</span>
                  <input
                    type="number"
                    value={initialCapital}
                    onChange={(e) => setInitialCapital(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span>Slippage (bps) / Mode</span>
                  <div style={{ display: "flex", gap: 4 }}>
                    <input
                      type="number"
                      step="0.5"
                      style={{ width: 72 }}
                      value={slippageBps}
                      onChange={(e) => setSlippageBps(e.target.value)}
                      title="Slippage (bps)"
                    />
                    <select
                      value={rotWalkForward ? "wf" : "full"}
                      onChange={(e) => setRotWalkForward(e.target.value === "wf")}
                    >
                      <option value="full">Full Period</option>
                      <option value="wf">Walk-Forward ({splitPct}%/{100 - (Number(splitPct) || 60)}%)</option>
                    </select>
                  </div>
                </label>
              </div>
              {rotWalkForward && (
                <div className="form-grid-2">
                  <label className="field">
                    <span>In-Sample Split (%)</span>
                    <input
                      type="number"
                      min={40}
                      max={85}
                      step={5}
                      value={splitPct}
                      onChange={(e) => setSplitPct(e.target.value)}
                    />
                  </label>
                  <label className="field">
                    <span>Tuning Mode</span>
                    <select
                      value={autoTune ? "auto" : "fixed"}
                      onChange={(e) => setAutoTune(e.target.value === "auto")}
                    >
                      <option value="auto">Auto-Tune Grid on In-Sample</option>
                      <option value="fixed">Test Fixed Params Split</option>
                    </select>
                  </label>
                </div>
              )}
            </>
          ) : mode !== "portfolio" ? (
            <>
              <div className="form-grid-4">
                <label className="field">
                  <span>Strategy</span>
                  <select
                    value={strategyType}
                    onChange={(e) => {
                      const st = e.target.value as StrategyType;
                      const preset = STRATEGY_PRESETS[st];
                      setStrategyType(st);
                      setTimeframe(preset.timeframe);
                      setStopLossPct(preset.stopLossPct);
                      setTakeProfitPct(preset.takeProfitPct);
                      setStrategyParams(defaultParamStrings(st));
                    }}
                  >
                    <option value="rsi2_pullback_swing">Daily 2-Period RSI Pullback Swing</option>
                    <option value="vwap_rsi_reversion">Intraday VWAP + RSI Mean Reversion</option>
                    <option value="shock_reversion">Overreaction Exhaustion (Price/Vol Shock)</option>
                    <option value="sma_crossover">SMA Crossover</option>
                  </select>
                </label>

                <label className="field">
                  <span>Symbol</span>
                  <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
                    {universe.map((sym) => (
                      <option key={sym} value={sym}>
                        {sym}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="field">
                  <span>Timeframe</span>
                  <select
                    value={timeframe}
                    onChange={(e) => setTimeframe(e.target.value as Timeframe)}
                  >
                    <option value="1 day">1 day (20y via yfinance / 10y IBKR)</option>
                    <option value="5 mins">5 mins (60d via yfinance / 30d IBKR)</option>
                    <option value="15 mins">15 mins (60d via yfinance / 60d IBKR)</option>
                    <option value="1 hour">1 hour (730d via yfinance / 180d IBKR)</option>
                  </select>
                </label>

                <label className="field">
                  <span>Data Source</span>
                  <select
                    value={source}
                    onChange={(e) => setSource(e.target.value as DataSource)}
                  >
                    <option value="yfinance">Yahoo Finance (Free)</option>
                    <option value="ibkr" disabled={!ibkrConnected}>
                      IBKR TWS/Gateway {ibkrConnected ? "(Connected)" : "(Connect first)"}
                    </option>
                  </select>
                </label>
              </div>

              <div className="form-grid-5">
                <label className="field">
                  <span>Initial Capital ($)</span>
                  <input
                    type="number"
                    value={initialCapital}
                    onChange={(e) => setInitialCapital(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span>Position Sizing</span>
                  <select
                    value={sizingMode}
                    onChange={(e) =>
                      setSizingMode(e.target.value as "pct_equity" | "fixed_shares")
                    }
                  >
                    <option value="pct_equity">% of Equity (Compounded)</option>
                    <option value="fixed_shares">Fixed Shares</option>
                  </select>
                </label>
                {sizingMode === "pct_equity" ? (
                  <label className="field">
                    <span>Allocation (% Equity)</span>
                    <input
                      type="number"
                      min={1}
                      max={100}
                      step="1"
                      value={allocationPct}
                      onChange={(e) => setAllocationPct(e.target.value)}
                    />
                  </label>
                ) : (
                  <label className="field">
                    <span>Qty / Trade (Shares)</span>
                    <input
                      type="number"
                      min={1}
                      value={quantity}
                      onChange={(e) => setQuantity(e.target.value)}
                    />
                  </label>
                )}
                <label className="field">
                  <span>Stop-Loss (%)</span>
                  <input
                    type="number"
                    step="0.1"
                    placeholder="None"
                    value={stopLossPct}
                    onChange={(e) => setStopLossPct(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span>Take-Profit (%) / Slippage</span>
                  <div style={{ display: "flex", gap: 4 }}>
                    <input
                      type="number"
                      step="0.1"
                      placeholder="TP %"
                      value={takeProfitPct}
                      onChange={(e) => setTakeProfitPct(e.target.value)}
                      title="Take-Profit (%)"
                    />
                    <input
                      type="number"
                      step="0.5"
                      value={slippageBps}
                      onChange={(e) => setSlippageBps(e.target.value)}
                      title="Slippage (bps)"
                    />
                  </div>
                </label>
              </div>

              <div className="form-grid-4">
                {STRATEGY_PARAM_FIELDS[strategyType].map((f) => (
                  <label key={f.key} className="field">
                    <span>{f.label}</span>
                    <input
                      type="number"
                      min={f.min}
                      step={f.step || "1"}
                      value={strategyParams[f.key] ?? String(f.defaultValue)}
                      onChange={(e) =>
                        setStrategyParams((prev) => ({
                          ...prev,
                          [f.key]: e.target.value,
                        }))
                      }
                    />
                  </label>
                ))}
              </div>

              {mode === "walk_forward" && (
                <div className="form-grid-3">
                  <label className="field">
                    <span>In-Sample Split (%)</span>
                    <input
                      type="number"
                      min={40}
                      max={85}
                      step={5}
                      value={splitPct}
                      onChange={(e) => setSplitPct(e.target.value)}
                    />
                  </label>
                  <label className="field">
                    <span>Tuning Mode</span>
                    <select
                      value={autoTune ? "auto" : "fixed"}
                      onChange={(e) => setAutoTune(e.target.value === "auto")}
                    >
                      <option value="auto">Auto-Tune Grid on In-Sample</option>
                      <option value="fixed">Test Fixed Params Split</option>
                    </select>
                  </label>
                  <div className="field">
                    <span>Out-of-Sample Window</span>
                    <div className="muted-sm" style={{ paddingTop: 6 }}>
                      Last {Math.max(15, 100 - (Number(splitPct) || 60))}% kept untouched for final scoring
                    </div>
                  </div>
                </div>
              )}
            </>
          ) : (
            <>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  flexWrap: "wrap",
                  gap: 8,
                }}
              >
                <span className="muted-sm">
                  1 bot per symbol sharing one compounding cash pool. Missing cached bars auto-sync from Yahoo Finance.
                </span>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="mini-btn"
                    onClick={() => setFleetBots(SWING_FLEET_PRESET)}
                  >
                    Load 5-ETF Swing Fleet
                  </button>
                  <button
                    type="button"
                    className="mini-btn"
                    onClick={() => setFleetBots(INTRADAY_FLEET_PRESET)}
                  >
                    Load Intraday Mixed Fleet
                  </button>
                </div>
              </div>

              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Bot ID</th>
                      <th>Symbol</th>
                      <th>Strategy</th>
                      <th>Timeframe</th>
                      <th className="num">
                        {fleetSizingMode === "pct_equity" ? "Alloc (% Equity)" : "Qty (Shares)"}
                      </th>
                      <th className="num">SL %</th>
                      <th className="num">TP %</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {fleetBots.map((b, idx) => (
                      <tr key={b.symbol}>
                        <td>
                          <strong>{b.bot_id}</strong>
                        </td>
                        <td>
                          <span className="symbol-pill">{b.symbol}</span>
                        </td>
                        <td>{b.strategy_type}</td>
                        <td>{b.timeframe}</td>
                        <td className="num">
                          {fleetSizingMode === "pct_equity" ? (
                            <input
                              type="number"
                              min={1}
                              max={100}
                              style={{ width: 64 }}
                              value={b.allocation_pct ?? 25}
                              onChange={(e) => {
                                const pct = Math.min(100, Math.max(1, Number(e.target.value) || 25));
                                setFleetBots((prev) =>
                                  prev.map((item, i) =>
                                    i === idx ? { ...item, allocation_pct: pct } : item
                                  )
                                );
                              }}
                            />
                          ) : (
                            <input
                              type="number"
                              min={1}
                              style={{ width: 64 }}
                              value={b.quantity}
                              onChange={(e) => {
                                const qty = Math.max(1, Number(e.target.value) || 1);
                                setFleetBots((prev) =>
                                  prev.map((item, i) =>
                                    i === idx ? { ...item, quantity: qty } : item
                                  )
                                );
                              }}
                            />
                          )}
                        </td>
                        <td className="num">{b.stop_loss_pct ?? "—"}</td>
                        <td className="num">{b.take_profit_pct ?? "—"}</td>
                        <td className="num">
                          <button
                            type="button"
                            className="mini-btn danger"
                            onClick={() =>
                              setFleetBots((prev) => prev.filter((_, i) => i !== idx))
                            }
                            title="Remove bot"
                          >
                            <Trash2 size={12} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {availableSymbols.length > 0 && (
                <div className="form-grid-5" style={{ alignItems: "end" }}>
                  <label className="field">
                    <span>Add Symbol</span>
                    <select value={newBotSym} onChange={(e) => setNewBotSym(e.target.value)}>
                      {availableSymbols.map((sym) => (
                        <option key={sym} value={sym}>
                          {sym}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field">
                    <span>Strategy</span>
                    <select
                      value={newBotStrat}
                      onChange={(e) => {
                        const st = e.target.value as StrategyType;
                        const preset = STRATEGY_PRESETS[st];
                        setNewBotStrat(st);
                        setNewBotTf(preset.timeframe);
                        setNewBotSl(preset.stopLossPct);
                        setNewBotTp(preset.takeProfitPct);
                      }}
                    >
                      <option value="rsi2_pullback_swing">RSI(2) Pullback Swing</option>
                      <option value="vwap_rsi_reversion">VWAP + RSI Reversion</option>
                      <option value="shock_reversion">Shock Reversion</option>
                      <option value="sma_crossover">SMA Crossover</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>Timeframe</span>
                    <select
                      value={newBotTf}
                      onChange={(e) => setNewBotTf(e.target.value as Timeframe)}
                    >
                      <option value="1 day">1 day</option>
                      <option value="5 mins">5 mins</option>
                      <option value="15 mins">15 mins</option>
                      <option value="1 hour">1 hour</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>
                      {fleetSizingMode === "pct_equity" ? "Alloc % / SL% / TP%" : "Qty / SL% / TP%"}
                    </span>
                    <div style={{ display: "flex", gap: 4 }}>
                      {fleetSizingMode === "pct_equity" ? (
                        <input
                          type="number"
                          min={1}
                          max={100}
                          value={newBotAlloc}
                          onChange={(e) => setNewBotAlloc(e.target.value)}
                          title="% of portfolio equity"
                        />
                      ) : (
                        <input
                          type="number"
                          min={1}
                          value={newBotQty}
                          onChange={(e) => setNewBotQty(e.target.value)}
                          title="Shares per trade"
                        />
                      )}
                      <input
                        type="number"
                        step="0.1"
                        placeholder="SL"
                        value={newBotSl}
                        onChange={(e) => setNewBotSl(e.target.value)}
                        title="Stop-loss %"
                      />
                      <input
                        type="number"
                        step="0.1"
                        placeholder="TP"
                        value={newBotTp}
                        onChange={(e) => setNewBotTp(e.target.value)}
                        title="Take-profit %"
                      />
                    </div>
                  </label>
                  <button
                    type="button"
                    className="btn-outline"
                    onClick={handleAddBotToFleet}
                  >
                    <Plus size={14} />
                    Add Bot
                  </button>
                </div>
              )}

              <div className="form-grid-3">
                <label className="field">
                  <span>Shared Initial Capital ($)</span>
                  <input
                    type="number"
                    value={initialCapital}
                    onChange={(e) => setInitialCapital(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span>Fleet Sizing Mode</span>
                  <select
                    value={fleetSizingMode}
                    onChange={(e) =>
                      setFleetSizingMode(e.target.value as "pct_equity" | "fixed_shares")
                    }
                  >
                    <option value="pct_equity">% of Equity (Compounded)</option>
                    <option value="fixed_shares">Fixed Shares</option>
                  </select>
                </label>
                <label className="field">
                  <span>Slippage (bps)</span>
                  <input
                    type="number"
                    step="0.5"
                    value={slippageBps}
                    onChange={(e) => setSlippageBps(e.target.value)}
                  />
                </label>
              </div>
            </>
          )}

          <button type="submit" disabled={running} className="btn-full btn-primary">
            <PlayCircle size={15} style={{ verticalAlign: "text-bottom", marginRight: 5 }} />
            {running
              ? "Running Historical Backtest..."
              : mode === "single"
                ? `Run Backtest on ${symbol} (${timeframe})`
                : mode === "walk_forward"
                  ? `Run Walk-Forward Split on ${symbol} (${timeframe})`
                  : mode === "rotation"
                    ? rotWalkForward
                      ? `Run Dual-Momentum Walk-Forward (${universe.length} symbols)`
                      : `Run Dual-Momentum Rotation (Top ${rotTopN} of ${universe.length})`
                    : `Run Portfolio Backtest (${fleetBots.length} bots)`}
          </button>

          {statusMsg && <div className="toast toast-ok">{statusMsg}</div>}
          {error && <div className="toast toast-err">{error}</div>}
        </form>
      </div>

      {activeWf && (
        <>
          <div className="card">
            <div className="card-header">
              <div>
                <h3>Walk-Forward Validation — {activeWf.title}</h3>
                <span className="muted-sm">
                  Split at {activeWf.splitTime} ({Math.round(activeWf.splitRatio * 100)}% In-Sample /{" "}
                  {Math.round((1 - activeWf.splitRatio) * 100)}% Out-of-Sample) ·{" "}
                  {activeWf.candidatesTested} candidate(s) tested
                </span>
              </div>
              <span
                className={`pill ${
                  activeWf.verdict === "ROBUST"
                    ? "pill-buy"
                    : activeWf.verdict === "OVERFIT_WARNING"
                      ? "pill-sell"
                      : "pill-muted"
                }`}
              >
                {activeWf.verdict.replaceAll("_", " ")}
              </span>
            </div>

            <div className="news-rationale" style={{ marginBottom: 12 }}>
              <strong>Verdict:</strong> {activeWf.verdictReason}{" "}
              {Object.keys(activeWf.selectedParams).length > 0 && (
                <span className="muted-sm">
                  · Selected params:{" "}
                  <code>{JSON.stringify(activeWf.selectedParams)}</code>
                </span>
              )}
            </div>

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Window</th>
                    <th className="num">Trades</th>
                    <th className="num">Return</th>
                    <th className="num">B&amp;H Return</th>
                    <th className="num">Max Drawdown</th>
                    <th className="num">B&amp;H Max DD</th>
                    <th className="num">Sharpe</th>
                    <th className="num">B&amp;H Sharpe</th>
                    <th className="num">Win Rate</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>
                      <strong>In-Sample (Tuning)</strong>
                    </td>
                    <td className="num">{activeWf.inSample.total_trades}</td>
                    <td
                      className={`num ${
                        activeWf.inSample.total_return_pct >= 0 ? "pos" : "neg"
                      }`}
                    >
                      {activeWf.inSample.total_return_pct >= 0 ? "+" : ""}
                      {activeWf.inSample.total_return_pct.toFixed(2)}%
                    </td>
                    <td className="num muted-sm">
                      {activeWf.inSample.benchmark
                        ? `${activeWf.inSample.benchmark.total_return_pct >= 0 ? "+" : ""}${activeWf.inSample.benchmark.total_return_pct.toFixed(2)}%`
                        : "—"}
                      {activeWf.inSample.benchmarks?.find((b) => b.label === "Buy & Hold (QQQ)") && (
                        <div>
                          QQQ:{" "}
                          {(() => {
                            const q = activeWf.inSample.benchmarks!.find(
                              (b) => b.label === "Buy & Hold (QQQ)"
                            )!;
                            return `${q.total_return_pct >= 0 ? "+" : ""}${q.total_return_pct.toFixed(2)}%`;
                          })()}
                        </div>
                      )}
                    </td>
                    <td className="num neg">-{activeWf.inSample.max_drawdown_pct.toFixed(2)}%</td>
                    <td className="num muted-sm">
                      {activeWf.inSample.benchmark
                        ? `-${activeWf.inSample.benchmark.max_drawdown_pct.toFixed(2)}%`
                        : "—"}
                      {activeWf.inSample.benchmarks?.find((b) => b.label === "Buy & Hold (QQQ)") && (
                        <div>
                          -
                          {activeWf.inSample.benchmarks
                            .find((b) => b.label === "Buy & Hold (QQQ)")!
                            .max_drawdown_pct.toFixed(2)}
                          %
                        </div>
                      )}
                    </td>
                    <td className="num">{activeWf.inSample.sharpe_ratio.toFixed(2)}</td>
                    <td className="num muted-sm">
                      {activeWf.inSample.benchmark
                        ? activeWf.inSample.benchmark.sharpe_ratio.toFixed(2)
                        : "—"}
                      {activeWf.inSample.benchmarks?.find((b) => b.label === "Buy & Hold (QQQ)") && (
                        <div>
                          {activeWf.inSample.benchmarks
                            .find((b) => b.label === "Buy & Hold (QQQ)")!
                            .sharpe_ratio.toFixed(2)}
                        </div>
                      )}
                    </td>
                    <td className="num">{activeWf.inSample.win_rate_pct.toFixed(1)}%</td>
                  </tr>
                  <tr>
                    <td>
                      <strong>Out-of-Sample (Untouched)</strong>
                    </td>
                    <td className="num">{activeWf.outOfSample.total_trades}</td>
                    <td
                      className={`num ${
                        activeWf.outOfSample.total_return_pct >= 0 ? "pos" : "neg"
                      }`}
                    >
                      {activeWf.outOfSample.total_return_pct >= 0 ? "+" : ""}
                      {activeWf.outOfSample.total_return_pct.toFixed(2)}%
                    </td>
                    <td className="num muted-sm">
                      {activeWf.outOfSample.benchmark
                        ? `${activeWf.outOfSample.benchmark.total_return_pct >= 0 ? "+" : ""}${activeWf.outOfSample.benchmark.total_return_pct.toFixed(2)}%`
                        : "—"}
                      {activeWf.outOfSample.benchmarks?.find((b) => b.label === "Buy & Hold (QQQ)") && (
                        <div>
                          QQQ:{" "}
                          {(() => {
                            const q = activeWf.outOfSample.benchmarks!.find(
                              (b) => b.label === "Buy & Hold (QQQ)"
                            )!;
                            return `${q.total_return_pct >= 0 ? "+" : ""}${q.total_return_pct.toFixed(2)}%`;
                          })()}
                        </div>
                      )}
                    </td>
                    <td className="num neg">
                      -{activeWf.outOfSample.max_drawdown_pct.toFixed(2)}%
                    </td>
                    <td className="num muted-sm">
                      {activeWf.outOfSample.benchmark
                        ? `-${activeWf.outOfSample.benchmark.max_drawdown_pct.toFixed(2)}%`
                        : "—"}
                      {activeWf.outOfSample.benchmarks?.find((b) => b.label === "Buy & Hold (QQQ)") && (
                        <div>
                          -
                          {activeWf.outOfSample.benchmarks
                            .find((b) => b.label === "Buy & Hold (QQQ)")!
                            .max_drawdown_pct.toFixed(2)}
                          %
                        </div>
                      )}
                    </td>
                    <td className="num">{activeWf.outOfSample.sharpe_ratio.toFixed(2)}</td>
                    <td className="num muted-sm">
                      {activeWf.outOfSample.benchmark
                        ? activeWf.outOfSample.benchmark.sharpe_ratio.toFixed(2)
                        : "—"}
                      {activeWf.outOfSample.benchmarks?.find((b) => b.label === "Buy & Hold (QQQ)") && (
                        <div>
                          {activeWf.outOfSample.benchmarks
                            .find((b) => b.label === "Buy & Hold (QQQ)")!
                            .sharpe_ratio.toFixed(2)}
                        </div>
                      )}
                    </td>
                    <td className="num">{activeWf.outOfSample.win_rate_pct.toFixed(1)}%</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <div className="form-grid-2">
            {wfIsSvg && (
              <div className="card">
                <div className="card-header">
                  <h3>In-Sample Curve (Tuning)</h3>
                  <span className="muted-sm">
                    ${wfIsSvg.minE.toLocaleString()} – ${wfIsSvg.maxE.toLocaleString()}
                  </span>
                </div>
                <svg viewBox={`0 0 ${wfIsSvg.w} ${wfIsSvg.h}`} className="price-svg">
                  {wfIsSvg.benchPts && (
                    <polyline
                      fill="none"
                      stroke="#60a5fa"
                      strokeWidth="1.8"
                      strokeDasharray="5 4"
                      opacity="0.85"
                      points={wfIsSvg.benchPts}
                    />
                  )}
                  {wfIsSvg.qqqPts && (
                    <polyline
                      fill="none"
                      stroke="#a855f7"
                      strokeWidth="1.8"
                      strokeDasharray="3 3"
                      opacity="0.85"
                      points={wfIsSvg.qqqPts}
                    />
                  )}
                  <polyline
                    fill="none"
                    stroke={activeWf.inSample.total_return_pct >= 0 ? "#10b981" : "#ef4444"}
                    strokeWidth="2.2"
                    points={wfIsSvg.pts}
                  />
                </svg>
              </div>
            )}

            {wfOosSvg && (
              <div className="card">
                <div className="card-header">
                  <h3>Out-of-Sample Curve (Test)</h3>
                  <span className="muted-sm">
                    ${wfOosSvg.minE.toLocaleString()} – ${wfOosSvg.maxE.toLocaleString()}
                  </span>
                </div>
                <svg viewBox={`0 0 ${wfOosSvg.w} ${wfOosSvg.h}`} className="price-svg">
                  {wfOosSvg.benchPts && (
                    <polyline
                      fill="none"
                      stroke="#60a5fa"
                      strokeWidth="1.8"
                      strokeDasharray="5 4"
                      opacity="0.85"
                      points={wfOosSvg.benchPts}
                    />
                  )}
                  {wfOosSvg.qqqPts && (
                    <polyline
                      fill="none"
                      stroke="#a855f7"
                      strokeWidth="1.8"
                      strokeDasharray="3 3"
                      opacity="0.85"
                      points={wfOosSvg.qqqPts}
                    />
                  )}
                  <polyline
                    fill="none"
                    stroke={activeWf.outOfSample.total_return_pct >= 0 ? "#10b981" : "#ef4444"}
                    strokeWidth="2.2"
                    points={wfOosSvg.pts}
                  />
                </svg>
              </div>
            )}
          </div>

          {(() => {
            const wfTrades = (
              wfTradesTab === "oos"
                ? activeWf.outOfSample.trades
                : activeWf.inSample.trades
            ) as (PortfolioBacktestTrade | (BacktestResult["trades"][number] & { bot_id?: string; symbol?: string }))[];
            const showBotCols = mode === "rotation";
            return (
              <div className="card">
                <div className="card-header">
                  <h3>
                    Simulated Trades ({wfTrades.length})
                  </h3>
                  <div className="nav-tabs">
                    <button
                      type="button"
                      className={`nav-tab ${wfTradesTab === "oos" ? "active" : ""}`}
                      onClick={() => setWfTradesTab("oos")}
                    >
                      Out-of-Sample ({activeWf.outOfSample.trades.length})
                    </button>
                    <button
                      type="button"
                      className={`nav-tab ${wfTradesTab === "is" ? "active" : ""}`}
                      onClick={() => setWfTradesTab("is")}
                    >
                      In-Sample ({activeWf.inSample.trades.length})
                    </button>
                  </div>
                </div>
                {wfTrades.length === 0 ? (
                  <div className="empty-box">No signals triggered during this window.</div>
                ) : (
                  <div className="table-wrap" style={{ maxHeight: 320, overflowY: "auto" }}>
                    <table>
                      <thead>
                        <tr>
                          {showBotCols && <th>Bot</th>}
                          {showBotCols && <th>Symbol</th>}
                          <th>Entry Time</th>
                          <th>Exit Time</th>
                          <th className="num">Qty</th>
                          <th className="num">Entry</th>
                          <th className="num">Exit</th>
                          <th className="num">P&amp;L</th>
                          <th className="num">Return</th>
                          <th>Exit Reason</th>
                        </tr>
                      </thead>
                      <tbody>
                        {wfTrades.map((t, i) => (
                          <tr key={i}>
                            {showBotCols && (
                              <td>
                                <strong>{t.bot_id}</strong>
                              </td>
                            )}
                            {showBotCols && (
                              <td>
                                <span className="symbol-pill">{t.symbol}</span>
                              </td>
                            )}
                            <td className="muted-sm">{t.entry_time}</td>
                            <td className="muted-sm">{t.exit_time}</td>
                            <td className="num">{t.quantity}</td>
                            <td className="num">${t.entry_price.toFixed(2)}</td>
                            <td className="num">${t.exit_price.toFixed(2)}</td>
                            <td className={`num ${t.pnl >= 0 ? "pos" : "neg"}`}>
                              {t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(2)}
                            </td>
                            <td className={`num ${t.return_pct >= 0 ? "pos" : "neg"}`}>
                              {t.return_pct >= 0 ? "+" : ""}
                              {t.return_pct.toFixed(2)}%
                            </td>
                            <td>{t.exit_reason}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            );
          })()}
        </>
      )}

      {activeSummary && (
        <>
          <section className="kpi-row">
            <div className="kpi-card">
              <span className="kpi-label">Final Equity</span>
              <strong className="kpi-value">${activeSummary.finalEquity.toLocaleString()}</strong>
              <span className="muted-sm">{activeSummary.barsTestedLabel}</span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">Total Return</span>
              <strong className={`kpi-value ${activeSummary.totalReturnPct >= 0 ? "pos" : "neg"}`}>
                {activeSummary.totalReturnPct >= 0 ? "+" : ""}
                {activeSummary.totalReturnPct.toFixed(2)}%
              </strong>
              <span className="muted-sm">
                {activeSummary.benchmark
                  ? `vs B&H ${activeSummary.benchmark.total_return_pct >= 0 ? "+" : ""}${activeSummary.benchmark.total_return_pct.toFixed(2)}%`
                  : `Initial $${activeSummary.initialCapital.toLocaleString()}`}
              </span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">Sharpe Ratio</span>
              <strong className="kpi-value">{activeSummary.sharpeRatio.toFixed(2)}</strong>
              <span className="muted-sm">
                {activeSummary.benchmark
                  ? `B&H ${activeSummary.benchmark.sharpe_ratio.toFixed(2)}`
                  : "Annualized estimate"}
              </span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">Max Drawdown</span>
              <strong className="kpi-value neg">-{activeSummary.maxDrawdownPct.toFixed(2)}%</strong>
              <span className="muted-sm">
                {activeSummary.benchmark
                  ? `B&H -${activeSummary.benchmark.max_drawdown_pct.toFixed(2)}%`
                  : "Peak-to-trough"}
              </span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">Win Rate / Exposure</span>
              <strong className="kpi-value">{activeSummary.winRatePct.toFixed(1)}%</strong>
              <span className="muted-sm">
                {activeSummary.totalTrades} trades · PF {activeSummary.profitFactor.toFixed(2)}
                {activeSummary.exposurePct !== undefined
                  ? ` · ${activeSummary.exposurePct.toFixed(1)}% in mkt`
                  : ""}
              </span>
            </div>
          </section>

          {activeSummary.benchmarks.length > 0 && (
            <div className="card">
              <div className="card-header">
                <h3>
                  {mode === "single" ? "Strategy" : "Portfolio"} vs. Benchmarks (SPY &amp; QQQ)
                </h3>
                <span
                  className={`pill ${
                    (activeSummary.alphaPct ?? 0) >= 0 ? "pill-buy" : "pill-muted"
                  }`}
                >
                  Excess vs. {activeSummary.benchmark?.label || "Benchmark"}:{" "}
                  {(activeSummary.alphaPct ?? 0) >= 0 ? "+" : ""}
                  {(activeSummary.alphaPct ?? 0).toFixed(2)}%
                </span>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Model</th>
                      <th className="num">Final Equity</th>
                      <th className="num">Total Return</th>
                      <th className="num">Max Drawdown</th>
                      <th className="num">Sharpe</th>
                      <th className="num">Time in Market</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>
                        <strong>{activeSummary.title}</strong>
                      </td>
                      <td className="num">${activeSummary.finalEquity.toLocaleString()}</td>
                      <td className={`num ${activeSummary.totalReturnPct >= 0 ? "pos" : "neg"}`}>
                        {activeSummary.totalReturnPct >= 0 ? "+" : ""}
                        {activeSummary.totalReturnPct.toFixed(2)}%
                      </td>
                      <td className="num neg">-{activeSummary.maxDrawdownPct.toFixed(2)}%</td>
                      <td className="num">{activeSummary.sharpeRatio.toFixed(2)}</td>
                      <td className="num">{(activeSummary.exposurePct ?? 0).toFixed(1)}%</td>
                    </tr>
                    {activeSummary.benchmarks.map((bm) => (
                      <tr key={bm.label}>
                        <td className="muted-sm">{bm.label} (100% invested)</td>
                        <td className="num">${bm.final_equity.toLocaleString()}</td>
                        <td className={`num ${bm.total_return_pct >= 0 ? "pos" : "neg"}`}>
                          {bm.total_return_pct >= 0 ? "+" : ""}
                          {bm.total_return_pct.toFixed(2)}%
                        </td>
                        <td className="num neg">-{bm.max_drawdown_pct.toFixed(2)}%</td>
                        <td className="num">{bm.sharpe_ratio.toFixed(2)}</td>
                        <td className="num">100.0%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {(mode === "portfolio" || (mode === "rotation" && !rotWfResult)) && portfolioResult && (
            <div className="card">
              <div className="card-header">
                <h3>Per-Bot Breakdown ({portfolioResult.bot_results.length} bots)</h3>
                <span className="muted-sm">
                  Shared ${portfolioResult.initial_capital.toLocaleString()} cash pool ·{" "}
                  {portfolioResult.skipped_for_cash} signals skipped for insufficient cash
                </span>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Bot</th>
                      <th>Symbol</th>
                      <th>Strategy</th>
                      <th>Timeframe</th>
                      <th className="num">Trades</th>
                      <th className="num">Win Rate</th>
                      <th className="num">In Market</th>
                      <th className="num">Cash Skipped</th>
                      <th className="num">Net P&amp;L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {portfolioResult.bot_results.map((br) => (
                      <tr key={br.bot_id}>
                        <td>
                          <strong>{br.bot_id}</strong>
                        </td>
                        <td>
                          <span className="symbol-pill">{br.symbol}</span>
                        </td>
                        <td>{br.strategy_type}</td>
                        <td>{br.timeframe}</td>
                        <td className="num">{br.total_trades}</td>
                        <td className="num">{br.win_rate_pct.toFixed(1)}%</td>
                        <td className="num">{br.exposure_pct.toFixed(1)}%</td>
                        <td className="num">{br.skipped_for_cash}</td>
                        <td className={`num ${br.net_pnl >= 0 ? "pos" : "neg"}`}>
                          {br.net_pnl >= 0 ? "+" : ""}${br.net_pnl.toFixed(2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {equitySvg && (
            <div className="card">
              <div className="card-header">
                <h3>Equity Curve — {activeSummary.title}</h3>
                <div className="equity-legend">
                  <span className="legend-item">
                    <span
                      className="legend-swatch"
                      style={{
                        background: activeSummary.totalReturnPct >= 0 ? "#10b981" : "#ef4444",
                      }}
                    />
                    {mode === "single" ? "Strategy" : "Portfolio"}
                  </span>
                  {equitySvg.benchPts && (
                    <span className="legend-item">
                      <span className="legend-swatch legend-dashed" />
                      {activeSummary.benchmark?.label || "Buy & Hold (SPY)"}
                    </span>
                  )}
                  {equitySvg.qqqPts && (
                    <span className="legend-item">
                      <span
                        className="legend-swatch"
                        style={{ background: "#a855f7" }}
                      />
                      Buy &amp; Hold (QQQ)
                    </span>
                  )}
                  <span className="muted-sm">
                    ${equitySvg.minE.toLocaleString()} – ${equitySvg.maxE.toLocaleString()}
                  </span>
                </div>
              </div>
              <svg viewBox={`0 0 ${equitySvg.w} ${equitySvg.h}`} className="price-svg">
                {equitySvg.benchPts && (
                  <polyline
                    fill="none"
                    stroke="#60a5fa"
                    strokeWidth="1.8"
                    strokeDasharray="5 4"
                    opacity="0.85"
                    points={equitySvg.benchPts}
                  />
                )}
                {equitySvg.qqqPts && (
                  <polyline
                    fill="none"
                    stroke="#a855f7"
                    strokeWidth="1.8"
                    strokeDasharray="3 3"
                    opacity="0.85"
                    points={equitySvg.qqqPts}
                  />
                )}
                <polyline
                  fill="none"
                  stroke={activeSummary.totalReturnPct >= 0 ? "#10b981" : "#ef4444"}
                  strokeWidth="2.2"
                  points={equitySvg.pts}
                />
              </svg>
            </div>
          )}

          <div className="card">
            <div className="card-header">
              <h3>Simulated Trades ({activeSummary.trades.length})</h3>
            </div>
            {activeSummary.trades.length === 0 ? (
              <div className="empty-box">No signals triggered during this period.</div>
            ) : (
              <div className="table-wrap" style={{ maxHeight: 320, overflowY: "auto" }}>
                <table>
                  <thead>
                    <tr>
                      {(mode === "portfolio" || mode === "rotation") && <th>Bot</th>}
                      {(mode === "portfolio" || mode === "rotation") && <th>Symbol</th>}
                      <th>Entry Time</th>
                      <th>Exit Time</th>
                      <th className="num">Qty</th>
                      <th className="num">Entry</th>
                      <th className="num">Exit</th>
                      <th className="num">P&amp;L</th>
                      <th className="num">Return</th>
                      <th>Exit Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activeSummary.trades.map((t, i) => (
                      <tr key={i}>
                        {(mode === "portfolio" || mode === "rotation") && (
                          <td>
                            <strong>{t.bot_id}</strong>
                          </td>
                        )}
                        {(mode === "portfolio" || mode === "rotation") && (
                          <td>
                            <span className="symbol-pill">{t.symbol}</span>
                          </td>
                        )}
                        <td className="muted-sm">{t.entry_time}</td>
                        <td className="muted-sm">{t.exit_time}</td>
                        <td className="num">{t.quantity}</td>
                        <td className="num">${t.entry_price.toFixed(2)}</td>
                        <td className="num">${t.exit_price.toFixed(2)}</td>
                        <td className={`num ${t.pnl >= 0 ? "pos" : "neg"}`}>
                          {t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(2)}
                        </td>
                        <td className={`num ${t.return_pct >= 0 ? "pos" : "neg"}`}>
                          {t.return_pct >= 0 ? "+" : ""}
                          {t.return_pct.toFixed(2)}%
                        </td>
                        <td>{t.exit_reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}

      {cache.length > 0 && (
        <div className="card">
          <div className="card-header">
            <h3>Cached SQLite Market Data ({cache.length} datasets)</h3>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Timeframe</th>
                  <th>Source</th>
                  <th className="num">Bars</th>
                  <th>Start</th>
                  <th>End</th>
                </tr>
              </thead>
              <tbody>
                {cache.map((c) => (
                  <tr key={`${c.symbol}-${c.timeframe}`}>
                    <td>
                      <strong>{c.symbol}</strong>
                    </td>
                    <td>{c.timeframe}</td>
                    <td>{c.source}</td>
                    <td className="num">{c.bar_count.toLocaleString()}</td>
                    <td className="muted-sm">{c.start_time}</td>
                    <td className="muted-sm">{c.end_time}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
