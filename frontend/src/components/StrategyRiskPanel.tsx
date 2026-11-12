import { useEffect, useState } from "react";
import { api } from "../api";
import type { ActivityEvent, RiskStatus, StrategyInfo } from "../types";

interface StrategyRiskPanelProps {
  strategies: StrategyInfo[];
  risk: RiskStatus;
  activity: ActivityEvent[];
  onRefresh: () => void;
  onOpenFleet: () => void;
}

export function StrategyRiskPanel({
  strategies,
  risk,
  activity,
  onRefresh,
  onOpenFleet,
}: StrategyRiskPanelProps) {
  const [tab, setTab] = useState<"strategies" | "risk" | "log">("strategies");

  const [maxQty, setMaxQty] = useState(String(risk.limits.max_order_qty));
  const [maxNotional, setMaxNotional] = useState(String(risk.limits.max_order_notional));
  const [maxPos, setMaxPos] = useState(String(risk.limits.max_position_shares));
  const [maxLoss, setMaxLoss] = useState(String(risk.limits.max_daily_loss));
  const [riskMsg, setRiskMsg] = useState<string | null>(null);

  useEffect(() => {
    setMaxQty(String(risk.limits.max_order_qty));
    setMaxNotional(String(risk.limits.max_order_notional));
    setMaxPos(String(risk.limits.max_position_shares));
    setMaxLoss(String(risk.limits.max_daily_loss));
  }, [
    risk.limits.max_order_qty,
    risk.limits.max_order_notional,
    risk.limits.max_position_shares,
    risk.limits.max_daily_loss,
  ]);

  async function toggleBot(bot: StrategyInfo) {
    if (bot.running) {
      await api.stopBot(bot.name);
    } else {
      await api.startBot(bot.name);
    }
    onRefresh();
  }

  async function saveRiskLimits(e: React.FormEvent) {
    e.preventDefault();
    setRiskMsg(null);
    await api.updateRiskLimits({
      max_order_qty: Number(maxQty),
      max_order_notional: Number(maxNotional),
      max_position_shares: Number(maxPos),
      max_daily_loss: Number(maxLoss),
    });
    setRiskMsg("Risk limits updated.");
    onRefresh();
  }

  return (
    <div className="card">
      <div className="card-header">
        <div className="tabs">
          <button
            className={`tab-btn ${tab === "strategies" ? "active" : ""}`}
            onClick={() => setTab("strategies")}
          >
            Bots ({strategies.filter((s) => s.running).length}/{strategies.length})
          </button>
          <button
            className={`tab-btn ${tab === "risk" ? "active" : ""}`}
            onClick={() => setTab("risk")}
          >
            Risk Guard
          </button>
          <button
            className={`tab-btn ${tab === "log" ? "active" : ""}`}
            onClick={() => setTab("log")}
          >
            Activity ({activity.length})
          </button>
        </div>
      </div>

      {tab === "strategies" && (
        <div className="panel-body">
          {strategies.map((bot) => (
            <div key={bot.name} className="bot-mini-card">
              <div className="strat-head">
                <div>
                  <strong>{bot.name}</strong>
                  <div className="muted-sm">
                    {bot.symbol} · {bot.timeframe} · SL {bot.stop_loss_pct ?? "—"}%
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => toggleBot(bot)}
                  className={`mini-btn ${bot.running ? "danger" : ""}`}
                >
                  {bot.running ? "Stop" : "Start"}
                </button>
              </div>
              {bot.last_signal && (
                <div className="signal-box">
                  <code>{bot.last_signal}</code>
                </div>
              )}
            </div>
          ))}
          <button type="button" className="btn-outline" onClick={onOpenFleet}>
            Manage &amp; Create Bots in Bot Fleet →
          </button>
        </div>
      )}

      {tab === "risk" && (
        <form className="panel-body" onSubmit={saveRiskLimits}>
          <div className="form-grid-2">
            <label className="field">
              <span>Max Order Qty</span>
              <input
                type="number"
                min={1}
                value={maxQty}
                onChange={(e) => setMaxQty(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Max Notional ($)</span>
              <input
                type="number"
                min={100}
                step="100"
                value={maxNotional}
                onChange={(e) => setMaxNotional(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Max Pos Shares</span>
              <input
                type="number"
                min={1}
                value={maxPos}
                onChange={(e) => setMaxPos(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Max Daily Loss ($)</span>
              <input
                type="number"
                min={50}
                step="50"
                value={maxLoss}
                onChange={(e) => setMaxLoss(e.target.value)}
              />
            </label>
          </div>
          <div className="muted-sm">
            Live Port Protection:{" "}
            {risk.limits.allow_live_trading ? "ENABLED" : "LOCKED (Paper/Simulator only)"}
          </div>
          <button type="submit" className="btn-full btn-primary">
            Save Risk Limits
          </button>
          {riskMsg && <div className="toast toast-ok">{riskMsg}</div>}
        </form>
      )}

      {tab === "log" && (
        <div className="activity-list">
          {activity.length === 0 ? (
            <div className="empty-box">No activity events yet.</div>
          ) : (
            activity.slice(0, 30).map((evt) => (
              <div key={evt.id} className={`activity-item lvl-${evt.level}`}>
                <span className="act-cat">[{evt.category}]</span>
                <span className="act-msg">{evt.message}</span>
                <span className="act-time">
                  {new Date(evt.timestamp).toLocaleTimeString()}
                </span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
