import { Fragment, useState } from "react";
import { Bot, Compass, Eye, Play, Plus, SlidersHorizontal, Square, Trash2 } from "lucide-react";
import { api } from "../api";
import type {
  BotDryRunPreview,
  DualMomentumPreview,
  StrategyInfo,
  StrategyType,
  Timeframe,
} from "../types";

interface BotFleetPanelProps {
  bots: StrategyInfo[];
  universe: string[];
  onRefresh: () => void;
}

const STRATEGY_LABELS: Record<StrategyType, string> = {
  vwap_rsi_reversion: "Intraday VWAP + RSI Mean Reversion",
  rsi2_pullback_swing: "Daily 2-Period RSI Pullback Swing",
  shock_reversion: "Overreaction Exhaustion + NewsGuard",
  sma_crossover: "SMA Crossover",
  dual_momentum: "Dual-Momentum Rotation (Universe)",
};

const STRATEGY_PRESETS: Record<
  StrategyType,
  { timeframe: Timeframe; stopLossPct: string; takeProfitPct: string }
> = {
  vwap_rsi_reversion: { timeframe: "5 mins", stopLossPct: "1.0", takeProfitPct: "1.2" },
  rsi2_pullback_swing: { timeframe: "1 day", stopLossPct: "3.0", takeProfitPct: "" },
  shock_reversion: { timeframe: "5 mins", stopLossPct: "1.5", takeProfitPct: "2.0" },
  sma_crossover: { timeframe: "5 mins", stopLossPct: "1.5", takeProfitPct: "3.0" },
  dual_momentum: { timeframe: "1 day", stopLossPct: "", takeProfitPct: "" },
};

interface ParamSpec {
  key: string;
  label: string;
  kind: "int" | "float" | "text" | "select";
  defaultVal: string;
  step?: string;
  min?: number;
  options?: { value: string; label: string }[];
}

const STRATEGY_PARAM_SPECS: Record<StrategyType, ParamSpec[]> = {
  vwap_rsi_reversion: [
    { key: "rsi_period", label: "RSI Period", kind: "int", defaultVal: "14", min: 2 },
    { key: "rsi_oversold", label: "RSI Oversold", kind: "float", defaultVal: "30", step: "0.5" },
    { key: "rsi_exit", label: "RSI Exit", kind: "float", defaultVal: "55", step: "0.5" },
    { key: "rsi_overbought", label: "RSI Overbought (Short)", kind: "float", defaultVal: "70", step: "0.5" },
    { key: "rsi_cover", label: "RSI Cover (Short)", kind: "float", defaultVal: "45", step: "0.5" },
    { key: "vwap_dev_pct", label: "VWAP Dev (%)", kind: "float", defaultVal: "0.45", step: "0.05", min: 0.05 },
  ],
  rsi2_pullback_swing: [
    { key: "rsi_period", label: "RSI Period", kind: "int", defaultVal: "2", min: 2 },
    { key: "rsi_buy", label: "RSI Buy ≤", kind: "float", defaultVal: "12", step: "0.5" },
    { key: "rsi_short", label: "RSI Short ≥", kind: "float", defaultVal: "88", step: "0.5" },
    { key: "trend_sma", label: "Trend SMA", kind: "int", defaultVal: "200", min: 20 },
    { key: "exit_sma", label: "Exit SMA", kind: "int", defaultVal: "5", min: 2 },
  ],
  shock_reversion: [
    { key: "shock_atr_mult", label: "Shock ATR Mult", kind: "float", defaultVal: "1.8", step: "0.1", min: 0.5 },
    { key: "vol_mult", label: "Volume Mult", kind: "float", defaultVal: "2.0", step: "0.1", min: 0.5 },
    { key: "vwap_dev_pct", label: "VWAP Dev (%)", kind: "float", defaultVal: "0.6", step: "0.05", min: 0.05 },
    { key: "cooldown_seconds", label: "Cooldown (s)", kind: "int", defaultVal: "300", min: 0 },
  ],
  sma_crossover: [
    { key: "fast_period", label: "Fast SMA", kind: "int", defaultVal: "5", min: 2 },
    { key: "slow_period", label: "Slow SMA", kind: "int", defaultVal: "15", min: 3 },
  ],
  dual_momentum: [
    { key: "top_n", label: "Top N", kind: "int", defaultVal: "3", min: 1 },
    {
      key: "momentum_mode",
      label: "Momentum Horizon",
      kind: "select",
      defaultVal: "blend_3_6_12",
      options: [
        { value: "blend_3_6_12", label: "3m + 6m + 12m Blend" },
        { value: "blend_1_3_6_12", label: "1m + 3m + 6m + 12m Blend" },
        { value: "single", label: "Single Lookback" },
      ],
    },
    { key: "lookback_days", label: "Lookback (d)", kind: "int", defaultVal: "126", min: 20 },
    { key: "trend_sma", label: "Trend SMA", kind: "int", defaultVal: "200", min: 20 },
    { key: "defensive_symbol", label: "Defensive ETF", kind: "text", defaultVal: "SHY" },
  ],
};

function formatBotBudget(bot: StrategyInfo): string {
  const parts: string[] = [];
  const pct = Number(bot.params?.max_allocation_pct);
  const cap = Number(bot.params?.max_capital);
  if (Number.isFinite(pct) && pct > 0) parts.push(`Max ${pct}%`);
  if (Number.isFinite(cap) && cap > 0) parts.push(`Max $${cap.toLocaleString()}`);
  if (parts.length > 0) return parts.join(" · ");
  if (bot.strategy_type === "dual_momentum") return "100% Equity";
  return `${bot.params?.quantity ?? 10} sh`;
}

export function BotFleetPanel({ bots, universe, onRefresh }: BotFleetPanelProps) {
  const [botId, setBotId] = useState("nvda-vwap-bot");
  const [strategyType, setStrategyType] = useState<StrategyType>("vwap_rsi_reversion");
  const [symbol, setSymbol] = useState(universe[0] || "SPY");
  const [timeframe, setTimeframe] = useState<Timeframe>(
    STRATEGY_PRESETS.vwap_rsi_reversion.timeframe
  );
  const [quantity, setQuantity] = useState("10");
  const [maxAllocationPct, setMaxAllocationPct] = useState("");
  const [maxCapital, setMaxCapital] = useState("");
  const [stopLossPct, setStopLossPct] = useState(
    STRATEGY_PRESETS.vwap_rsi_reversion.stopLossPct
  );
  const [takeProfitPct, setTakeProfitPct] = useState(
    STRATEGY_PRESETS.vwap_rsi_reversion.takeProfitPct
  );
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<DualMomentumPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [botPreview, setBotPreview] = useState<BotDryRunPreview | null>(null);
  const [botPreviewLoadingId, setBotPreviewLoadingId] = useState<string | null>(null);

  const [editingBotId, setEditingBotId] = useState<string | null>(null);
  const [editQty, setEditQty] = useState("10");
  const [editMaxPct, setEditMaxPct] = useState("");
  const [editMaxCap, setEditMaxCap] = useState("");
  const [editStopLoss, setEditStopLoss] = useState("");
  const [editTakeProfit, setEditTakeProfit] = useState("");
  const [editCollarPct, setEditCollarPct] = useState("0.25");
  const [editParams, setEditParams] = useState<Record<string, string>>({});
  const [editAllowShort, setEditAllowShort] = useState(false);
  const [editEtfsOnly, setEditEtfsOnly] = useState(false);
  const [editVolAdjusted, setEditVolAdjusted] = useState(false);
  const [budgetSaving, setBudgetSaving] = useState(false);

  // Create form Dual-Momentum params
  const [dmTopN, setDmTopN] = useState("3");
  const [dmMomentumMode, setDmMomentumMode] = useState<
    "single" | "blend_3_6_12" | "blend_1_3_6_12"
  >("blend_3_6_12");
  const [dmLookback, setDmLookback] = useState("126");
  const [dmTrendSma, setDmTrendSma] = useState("200");
  const [dmDefensive, setDmDefensive] = useState("SHY");
  const [dmEtfsOnly, setDmEtfsOnly] = useState(false);
  const [dmVolAdjusted, setDmVolAdjusted] = useState(true);
  const [allowShort, setAllowShort] = useState(false);

  function openBudgetEditor(bot: StrategyInfo) {
    if (editingBotId === bot.name) {
      setEditingBotId(null);
      return;
    }
    setEditingBotId(bot.name);
    setEditQty(String(bot.params?.quantity ?? 10));
    setEditMaxPct(bot.params?.max_allocation_pct ? String(bot.params.max_allocation_pct) : "");
    setEditMaxCap(bot.params?.max_capital ? String(bot.params.max_capital) : "");
    setEditStopLoss(bot.stop_loss_pct != null ? String(bot.stop_loss_pct) : "");
    setEditTakeProfit(bot.take_profit_pct != null ? String(bot.take_profit_pct) : "");
    setEditCollarPct(String(bot.params?.limit_collar_pct ?? 0.25));
    const specs = STRATEGY_PARAM_SPECS[bot.strategy_type] || [];
    const nextParams: Record<string, string> = {};
    for (const sp of specs) {
      const val = bot.params?.[sp.key];
      nextParams[sp.key] = val != null ? String(val) : sp.defaultVal;
    }
    setEditParams(nextParams);
    setEditAllowShort(Boolean(bot.params?.allow_short));
    setEditEtfsOnly(Boolean(bot.params?.etfs_only));
    setEditVolAdjusted(Boolean(bot.params?.volatility_adjusted));
  }

  async function saveBotBudget(bot: StrategyInfo) {
    setBudgetSaving(true);
    setError(null);
    try {
      const parsedParams: Record<string, number | string | boolean> = {
        limit_collar_pct: Math.max(0.01, Number(editCollarPct) || 0.25),
      };
      for (const sp of STRATEGY_PARAM_SPECS[bot.strategy_type] || []) {
        const raw = (editParams[sp.key] ?? sp.defaultVal).trim();
        if (sp.kind === "select") {
          parsedParams[sp.key] = raw;
        } else if (sp.kind === "text") {
          parsedParams[sp.key] = raw.toUpperCase();
        } else if (sp.kind === "int") {
          parsedParams[sp.key] = Math.round(Number(raw) || Number(sp.defaultVal));
        } else {
          parsedParams[sp.key] = Number(raw) || Number(sp.defaultVal);
        }
      }
      if (
        bot.strategy_type === "rsi2_pullback_swing" ||
        bot.strategy_type === "vwap_rsi_reversion"
      ) {
        parsedParams.allow_short = editAllowShort;
      }
      if (bot.strategy_type === "dual_momentum") {
        parsedParams.etfs_only = editEtfsOnly;
        parsedParams.volatility_adjusted = editVolAdjusted;
      }
      await api.updateBotBudget(bot.name, {
        quantity:
          bot.strategy_type === "dual_momentum"
            ? null
            : Math.max(1, Number(editQty) || 10),
        max_allocation_pct: editMaxPct.trim() ? Number(editMaxPct) : null,
        max_capital: editMaxCap.trim() ? Number(editMaxCap) : null,
        stop_loss_pct: editStopLoss.trim() ? Number(editStopLoss) : null,
        take_profit_pct: editTakeProfit.trim() ? Number(editTakeProfit) : null,
        params: parsedParams,
      });
      setEditingBotId(null);
      onRefresh();
      if (preview && bot.strategy_type === "dual_momentum") {
        void handlePreviewRotation();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update bot params");
    } finally {
      setBudgetSaving(false);
    }
  }

  async function handlePreviewBot(bot: StrategyInfo) {
    setBotPreviewLoadingId(bot.name);
    setError(null);
    try {
      const res = await api.previewBot(bot.name);
      setBotPreview(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to preview bot '${bot.name}'`);
    } finally {
      setBotPreviewLoadingId(null);
    }
  }

  async function handlePreviewRotation() {
    setPreviewLoading(true);
    setError(null);
    try {
      const res = await api.previewDualMomentum();
      setPreview(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to preview Dual-Momentum picks");
    } finally {
      setPreviewLoading(false);
    }
  }

  async function handleCreateBot(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const botParams: Record<string, number | string | boolean> =
        strategyType === "dual_momentum"
          ? {
              top_n: Math.max(1, Number(dmTopN) || 3),
              momentum_mode: dmMomentumMode,
              lookback_days: Math.max(20, Number(dmLookback) || 126),
              trend_sma: Math.max(20, Number(dmTrendSma) || 200),
              defensive_symbol: dmDefensive.trim().toUpperCase(),
              etfs_only: dmEtfsOnly,
              volatility_adjusted: dmVolAdjusted,
            }
          : {
              quantity: Math.max(1, Number(quantity) || 10),
              ...(strategyType === "rsi2_pullback_swing" ||
              strategyType === "vwap_rsi_reversion"
                ? { allow_short: allowShort }
                : {}),
            };

      await api.createBot({
        bot_id: botId.trim().toLowerCase(),
        strategy_type: strategyType,
        symbol: symbol.trim().toUpperCase(),
        timeframe,
        stop_loss_pct: stopLossPct ? Number(stopLossPct) : null,
        take_profit_pct: takeProfitPct ? Number(takeProfitPct) : null,
        params: botParams,
        max_allocation_pct: maxAllocationPct.trim() ? Number(maxAllocationPct) : null,
        max_capital: maxCapital.trim() ? Number(maxCapital) : null,
      });
      setBotId(`${symbol.toLowerCase()}-bot-${Math.floor(Math.random() * 90 + 10)}`);
      onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create bot");
    }
  }

  async function toggleBot(bot: StrategyInfo) {
    if (bot.running) {
      await api.stopBot(bot.name);
    } else {
      await api.startBot(bot.name);
    }
    onRefresh();
  }

  async function removeBot(name: string) {
    await api.deleteBot(name);
    onRefresh();
  }

  return (
    <div className="fleet-layout">
      <div className="card">
        <div className="card-header">
          <h3>
            <Bot size={16} style={{ verticalAlign: "text-bottom", marginRight: 6 }} />
            Automated Bot Fleet ({bots.length})
          </h3>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <span className="muted-sm">
              {bots.filter((b) => b.running).length} active · All orders pass RiskGuard + Server-Side Bracket Stops
            </span>
            <button
              type="button"
              className="btn-outline"
              onClick={handlePreviewRotation}
              disabled={previewLoading}
            >
              <Compass size={14} />
              {previewLoading ? "Scanning Universe..." : "Preview Dual-Momentum Picks"}
            </button>
          </div>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Bot ID</th>
                <th>Strategy</th>
                <th>Symbol</th>
                <th>Timeframe</th>
                <th>Budget / Sizing</th>
                <th>Bracket SL / TP</th>
                <th>Trades</th>
                <th>Last Signal</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {bots.map((bot) => (
                <Fragment key={bot.name}>
                  <tr>
                    <td>
                      <strong>{bot.name}</strong>
                    </td>
                    <td>{STRATEGY_LABELS[bot.strategy_type] || bot.strategy_type}</td>
                    <td>
                      <span className="symbol-pill">{bot.symbol}</span>
                    </td>
                    <td>{bot.timeframe}</td>
                    <td className="muted-sm">{formatBotBudget(bot)}</td>
                    <td className="muted-sm">
                      SL {bot.stop_loss_pct ? `${bot.stop_loss_pct}%` : "—"} · TP{" "}
                      {bot.take_profit_pct ? `${bot.take_profit_pct}%` : "—"}
                    </td>
                    <td>{bot.trades_placed}</td>
                    <td className="muted-sm" style={{ maxWidth: 280 }}>
                      {bot.last_signal || "Not evaluated yet"}
                    </td>
                    <td>
                      <span className={`pill ${bot.running ? "pill-buy" : "pill-muted"}`}>
                        {bot.running ? "RUNNING" : "STOPPED"}
                      </span>
                    </td>
                    <td className="num">
                      <div style={{ display: "inline-flex", gap: 6 }}>
                        <button
                          className="mini-btn"
                          title="Dry-run preview orders without placing"
                          disabled={botPreviewLoadingId === bot.name}
                          onClick={() => handlePreviewBot(bot)}
                        >
                          <Eye size={11} />{" "}
                          {botPreviewLoadingId === bot.name ? "..." : "Preview"}
                        </button>
                        <button
                          className="mini-btn"
                          title="Edit bot parameters & budget"
                          onClick={() => openBudgetEditor(bot)}
                        >
                          <SlidersHorizontal size={11} /> Edit
                        </button>
                        <button
                          className={`mini-btn ${bot.running ? "danger" : ""}`}
                          onClick={() => toggleBot(bot)}
                        >
                          {bot.running ? (
                            <>
                              <Square size={11} /> Stop
                            </>
                          ) : (
                            <>
                              <Play size={11} /> Start
                            </>
                          )}
                        </button>
                        {!bot.running && (
                          <button
                            className="mini-btn"
                            title="Delete bot"
                            onClick={() => removeBot(bot.name)}
                          >
                            <Trash2 size={12} />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                  {editingBotId === bot.name && (
                    <tr>
                      <td colSpan={10}>
                        <div
                          style={{
                            display: "flex",
                            flexWrap: "wrap",
                            gap: 12,
                            alignItems: "flex-end",
                            padding: "8px 4px",
                          }}
                        >
                          <label className="field" style={{ minWidth: 140 }}>
                            <span>Max % Equity</span>
                            <input
                              type="number"
                              min={1}
                              max={100}
                              step="1"
                              placeholder="Optional (e.g. 25)"
                              value={editMaxPct}
                              onChange={(e) => setEditMaxPct(e.target.value)}
                            />
                          </label>
                          <label className="field" style={{ minWidth: 150 }}>
                            <span>Max $ Amount</span>
                            <input
                              type="number"
                              min={1}
                              step="any"
                              placeholder="Optional (e.g. 2500)"
                              value={editMaxCap}
                              onChange={(e) => setEditMaxCap(e.target.value)}
                            />
                          </label>
                          <label className="field" style={{ minWidth: 110 }}>
                            <span>Stop-Loss (%)</span>
                            <input
                              type="number"
                              min={0.1}
                              step="0.1"
                              placeholder="Optional"
                              value={editStopLoss}
                              onChange={(e) => setEditStopLoss(e.target.value)}
                            />
                          </label>
                          <label className="field" style={{ minWidth: 110 }}>
                            <span>Take-Profit (%)</span>
                            <input
                              type="number"
                              min={0.1}
                              step="0.1"
                              placeholder="Optional"
                              value={editTakeProfit}
                              onChange={(e) => setEditTakeProfit(e.target.value)}
                            />
                          </label>
                          <label className="field" style={{ minWidth: 110 }}>
                            <span>Limit Collar (%)</span>
                            <input
                              type="number"
                              min={0.01}
                              step="0.05"
                              value={editCollarPct}
                              onChange={(e) => setEditCollarPct(e.target.value)}
                            />
                          </label>
                          {bot.strategy_type !== "dual_momentum" && (
                            <label className="field" style={{ minWidth: 115 }}>
                              <span>Default Qty (sh)</span>
                              <input
                                type="number"
                                min={1}
                                value={editQty}
                                onChange={(e) => setEditQty(e.target.value)}
                              />
                            </label>
                          )}
                          {(STRATEGY_PARAM_SPECS[bot.strategy_type] || [])
                            .filter(
                              (sp) =>
                                !(
                                  bot.strategy_type === "dual_momentum" &&
                                  sp.key === "lookback_days" &&
                                  (editParams.momentum_mode ?? "blend_3_6_12") !== "single"
                                )
                            )
                            .map((sp) => (
                            <label key={sp.key} className="field" style={{ minWidth: 115 }}>
                              <span>{sp.label}</span>
                              {sp.kind === "select" && sp.options ? (
                                <select
                                  value={editParams[sp.key] ?? sp.defaultVal}
                                  onChange={(e) =>
                                    setEditParams((prev) => ({ ...prev, [sp.key]: e.target.value }))
                                  }
                                >
                                  {sp.options.map((opt) => (
                                    <option key={opt.value} value={opt.value}>
                                      {opt.label}
                                    </option>
                                  ))}
                                </select>
                              ) : (
                                <input
                                  type={sp.kind === "text" ? "text" : "number"}
                                  min={sp.min}
                                  step={sp.step ?? (sp.kind === "int" ? "1" : "any")}
                                  value={editParams[sp.key] ?? sp.defaultVal}
                                  onChange={(e) =>
                                    setEditParams((prev) => ({ ...prev, [sp.key]: e.target.value }))
                                  }
                                />
                              )}
                            </label>
                          ))}
                          {(bot.strategy_type === "rsi2_pullback_swing" ||
                            bot.strategy_type === "vwap_rsi_reversion") && (
                            <label
                              className="field"
                              style={{ minWidth: 120, flexDirection: "row", alignItems: "center", gap: 6 }}
                            >
                              <input
                                type="checkbox"
                                checked={editAllowShort}
                                onChange={(e) => setEditAllowShort(e.target.checked)}
                                style={{ width: "auto" }}
                              />
                              <span>Allow Short</span>
                            </label>
                          )}
                          {bot.strategy_type === "dual_momentum" && (
                            <>
                              <label
                                className="field"
                                style={{ minWidth: 120, flexDirection: "row", alignItems: "center", gap: 6 }}
                              >
                                <input
                                  type="checkbox"
                                  checked={editEtfsOnly}
                                  onChange={(e) => setEditEtfsOnly(e.target.checked)}
                                  style={{ width: "auto" }}
                                />
                                <span>ETFs Only</span>
                              </label>
                              <label
                                className="field"
                                style={{ minWidth: 150, flexDirection: "row", alignItems: "center", gap: 6 }}
                              >
                                <input
                                  type="checkbox"
                                  checked={editVolAdjusted}
                                  onChange={(e) => setEditVolAdjusted(e.target.checked)}
                                  style={{ width: "auto" }}
                                />
                                <span>Volatility Balance</span>
                              </label>
                            </>
                          )}
                          <button
                            type="button"
                            className="btn-primary"
                            disabled={budgetSaving}
                            onClick={() => saveBotBudget(bot)}
                          >
                            {budgetSaving ? "Saving..." : "Save Params"}
                          </button>
                          <button
                            type="button"
                            className="btn-outline"
                            onClick={() => setEditingBotId(null)}
                          >
                            Cancel
                          </button>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {botPreview && (
        <div className="card">
          <div className="card-header">
            <div>
              <h3>
                Bot Dry-Run Preview — {botPreview.bot_id} ({botPreview.orders.length} order
                {botPreview.orders.length === 1 ? "" : "s"})
              </h3>
              <span className="muted-sm">
                {STRATEGY_LABELS[botPreview.strategy_type] || botPreview.strategy_type} ·{" "}
                {botPreview.timeframe} · Signal: <strong>{botPreview.signal}</strong>
              </span>
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span
                className={`pill ${
                  botPreview.market_session === "OPEN" ? "pill-buy" : "pill-muted"
                }`}
              >
                Market: {botPreview.market_session} ({botPreview.market_note})
              </span>
              <button
                type="button"
                className="mini-btn"
                onClick={() => setBotPreview(null)}
              >
                Dismiss
              </button>
            </div>
          </div>

          {botPreview.rationale && (
            <div className="muted-sm" style={{ padding: "4px 0 10px" }}>
              <strong>Why:</strong> {botPreview.rationale}
            </div>
          )}

          {botPreview.orders.length === 0 ? (
            <div className="muted-sm" style={{ padding: "8px 0" }}>
              No orders would be placed right now (HOLD / waiting for entry or exit signal).
            </div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Side</th>
                    <th className="num">Qty</th>
                    <th>Type</th>
                    <th className="num">Est. Price</th>
                    <th className="num">Est. Notional</th>
                    <th>Bracket SL / TP</th>
                    <th>RiskGuard</th>
                    <th>Rationale</th>
                  </tr>
                </thead>
                <tbody>
                  {botPreview.orders.map((ord, idx) => (
                    <tr key={`${ord.symbol}-${ord.side}-${idx}`}>
                      <td>
                        <span className="symbol-pill">{ord.symbol}</span>
                      </td>
                      <td>
                        <span
                          className={`pill ${ord.side === "BUY" ? "pill-buy" : "pill-sell"}`}
                        >
                          {ord.side}
                        </span>
                      </td>
                      <td className="num">{ord.quantity}</td>
                      <td>
                        {ord.order_type}
                        {ord.limit_price != null ? ` @ $${ord.limit_price.toFixed(2)}` : ""}
                      </td>
                      <td className="num">
                        {ord.estimated_price != null ? `$${ord.estimated_price.toFixed(2)}` : "—"}
                      </td>
                      <td className="num">
                        {ord.estimated_notional != null
                          ? `$${ord.estimated_notional.toLocaleString()}`
                          : "—"}
                      </td>
                      <td className="muted-sm">
                        SL {ord.stop_loss_price != null ? `$${ord.stop_loss_price.toFixed(2)}` : "—"} ·
                        TP {ord.take_profit_price != null ? `$${ord.take_profit_price.toFixed(2)}` : "—"}
                      </td>
                      <td>
                        <span
                          className={`pill ${
                            ord.risk_status === "PASS" ? "pill-buy" : "pill-sell"
                          }`}
                        >
                          {ord.risk_status}
                        </span>
                      </td>
                      <td className="muted-sm" style={{ maxWidth: 320 }}>
                        {ord.rationale}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {preview && (
        <div className="card">
          <div className="card-header">
            <div>
              <h3>Dual-Momentum Dry-Run Preview (No Orders Placed)</h3>
              <span className="muted-sm">
                As of {preview.as_of || "latest"} ({preview.source}) · Top {preview.top_n} by{" "}
                {preview.momentum_mode === "blend_3_6_12"
                  ? "3m+6m+12m blend"
                  : preview.momentum_mode === "blend_1_3_6_12"
                    ? "1m+3m+6m+12m blend"
                    : `${preview.lookback_days}d return`}{" "}
                above {preview.trend_sma}-SMA · Equity ${preview.account_equity.toLocaleString()}
              </span>
            </div>
            <span className="pill pill-buy">{preview.summary}</span>
          </div>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Role</th>
                  <th className="num">
                    {preview.momentum_mode === "blend_3_6_12"
                      ? "3/6/12m Return"
                      : preview.momentum_mode === "blend_1_3_6_12"
                        ? "1/3/6/12m Return"
                        : `${preview.lookback_days}d Return`}
                  </th>
                  <th className="num">Last Price</th>
                  <th className="num">{preview.trend_sma}-SMA</th>
                  <th>Passes Filter</th>
                  <th className="num">Target Weight</th>
                  <th className="num">Est. Shares</th>
                </tr>
              </thead>
              <tbody>
                {preview.rankings.map((r) => (
                  <tr key={r.symbol}>
                    <td>
                      <span className="symbol-pill">{r.symbol}</span>
                    </td>
                    <td className="muted-sm">{r.is_defensive ? "Defensive" : "Offensive"}</td>
                    <td className={`num ${r.trailing_return_pct >= 0 ? "pos" : "neg"}`}>
                      {r.trailing_return_pct >= 0 ? "+" : ""}
                      {r.trailing_return_pct.toFixed(2)}%
                    </td>
                    <td className="num">${r.last_close.toFixed(2)}</td>
                    <td className="num">${r.trend_sma.toFixed(2)}</td>
                    <td>
                      <span className={`pill ${r.passes_filter ? "pill-buy" : "pill-muted"}`}>
                        {r.passes_filter ? "PASS" : "FAIL"}
                      </span>
                    </td>
                    <td className="num">
                      {r.target_weight_pct > 0 ? (
                        <strong>{r.target_weight_pct.toFixed(1)}%</strong>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="num">{r.estimated_shares > 0 ? r.estimated_shares : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-header">
          <h3>Create New Trading Bot</h3>
          <span className="muted-sm">US Stocks &amp; ETFs Starter Universe</span>
        </div>
        <form className="order-form" onSubmit={handleCreateBot}>
          <div className="form-grid-2">
            <label className="field">
              <span>Bot ID</span>
              <input
                value={botId}
                onChange={(e) => setBotId(e.target.value)}
                required
              />
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
          </div>

          <div className="form-grid-2">
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
                }}
              >
                <option value="vwap_rsi_reversion">Intraday VWAP + RSI Mean Reversion</option>
                <option value="rsi2_pullback_swing">Daily 2-Period RSI Pullback Swing</option>
                <option value="shock_reversion">Overreaction Exhaustion + NewsGuard</option>
                <option value="sma_crossover">SMA Crossover</option>
                <option value="dual_momentum">Dual-Momentum Rotation (Universe)</option>
              </select>
            </label>
            <label className="field">
              <span>Timeframe</span>
              <select
                value={timeframe}
                onChange={(e) => setTimeframe(e.target.value as Timeframe)}
              >
                <option value="5 mins">5 mins (Intraday)</option>
                <option value="15 mins">15 mins (Intraday)</option>
                <option value="1 hour">1 hour</option>
                <option value="1 day">1 day (Swing)</option>
              </select>
            </label>
          </div>

          {strategyType === "dual_momentum" ? (
            <>
              <div className="form-grid-4">
                <label className="field">
                  <span>Top N Holdings</span>
                  <input
                    type="number"
                    min={1}
                    max={10}
                    value={dmTopN}
                    onChange={(e) => setDmTopN(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span>Momentum Horizon</span>
                  <select
                    value={dmMomentumMode}
                    onChange={(e) =>
                      setDmMomentumMode(
                        e.target.value as "single" | "blend_3_6_12" | "blend_1_3_6_12"
                      )
                    }
                  >
                    <option value="blend_3_6_12">3m + 6m + 12m Blend</option>
                    <option value="blend_1_3_6_12">1m + 3m + 6m + 12m Blend</option>
                    <option value="single">Single Lookback</option>
                  </select>
                </label>
                {dmMomentumMode === "single" && (
                  <label className="field">
                    <span>Lookback (days)</span>
                    <input
                      type="number"
                      min={20}
                      max={252}
                      value={dmLookback}
                      onChange={(e) => setDmLookback(e.target.value)}
                    />
                  </label>
                )}
                <label className="field">
                  <span>Trend SMA</span>
                  <input
                    type="number"
                    min={20}
                    max={252}
                    value={dmTrendSma}
                    onChange={(e) => setDmTrendSma(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span>Defensive ETF</span>
                  <input
                    value={dmDefensive}
                    placeholder="SHY"
                    onChange={(e) => setDmDefensive(e.target.value)}
                  />
                </label>
              </div>
              <div className="form-grid-2">
                <label className="field">
                  <span>Universe Scope</span>
                  <select
                    value={dmEtfsOnly ? "etfs" : "mixed"}
                    onChange={(e) => setDmEtfsOnly(e.target.value === "etfs")}
                  >
                    <option value="mixed">Mixed (12 US ETFs + 6 Tech Stocks)</option>
                    <option value="etfs">ETFs Only (12 US Sector &amp; Asset ETFs)</option>
                  </select>
                </label>
                <label
                  className="field"
                  style={{ flexDirection: "row", alignItems: "center", gap: 8, paddingTop: 20 }}
                >
                  <input
                    type="checkbox"
                    checked={dmVolAdjusted}
                    onChange={(e) => setDmVolAdjusted(e.target.checked)}
                    style={{ width: "auto" }}
                  />
                  <span>Volatility Balance (Return/Vol + Inverse-Vol Sizing)</span>
                </label>
              </div>
            </>
          ) : (
            <div className="form-grid-3">
              <label className="field">
                <span>Order Qty (shares)</span>
                <input
                  type="number"
                  min={1}
                  value={quantity}
                  onChange={(e) => setQuantity(e.target.value)}
                  required
                />
              </label>
              <label className="field">
                <span>Server Stop-Loss (%)</span>
                <input
                  type="number"
                  step="0.1"
                  min="0.1"
                  placeholder="e.g. 1.5"
                  value={stopLossPct}
                  onChange={(e) => setStopLossPct(e.target.value)}
                />
              </label>
              <label className="field">
                <span>Take-Profit (%)</span>
                <input
                  type="number"
                  step="0.1"
                  min="0.1"
                  placeholder="Optional"
                  value={takeProfitPct}
                  onChange={(e) => setTakeProfitPct(e.target.value)}
                />
              </label>
            </div>
          )}

          <div className="form-grid-2">
            <label className="field">
              <span>Max % Equity (optional)</span>
              <input
                type="number"
                min={1}
                max={100}
                step="1"
                placeholder="e.g. 25"
                value={maxAllocationPct}
                onChange={(e) => setMaxAllocationPct(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Max $ Amount (optional)</span>
              <input
                type="number"
                min={1}
                step="any"
                placeholder="e.g. 2500"
                value={maxCapital}
                onChange={(e) => setMaxCapital(e.target.value)}
              />
            </label>
          </div>

          {(strategyType === "rsi2_pullback_swing" ||
            strategyType === "vwap_rsi_reversion") && (
            <label
              className="field"
              style={{ flexDirection: "row", alignItems: "center", gap: 8 }}
            >
              <input
                type="checkbox"
                checked={allowShort}
                onChange={(e) => setAllowShort(e.target.checked)}
                style={{ width: "auto" }}
              />
              <span>
                Allow Shorting (
                {strategyType === "rsi2_pullback_swing"
                  ? "short RSI2 >= 88 when Close < SMA200"
                  : "short overbought spikes above VWAP"}
                )
              </span>
            </label>
          )}

          <button type="submit" className="btn-full btn-primary">
            <Plus size={14} style={{ verticalAlign: "text-bottom", marginRight: 4 }} />
            Add Bot to Fleet
          </button>
          {error && <div className="toast toast-err">{error}</div>}
        </form>
      </div>
    </div>
  );
}

