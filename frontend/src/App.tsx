import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  AlertOctagon,
  Bot,
  FlaskConical,
  LayoutDashboard,
  Newspaper,
  Plug,
  Plus,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  TrendingUp,
  Wifi,
  WifiOff,
  X,
} from "lucide-react";
import { api } from "./api";
import { BacktestLab } from "./components/BacktestLab";
import { BotFleetPanel } from "./components/BotFleetPanel";
import { OrderTicket } from "./components/OrderTicket";
import { PositionsOrders } from "./components/PositionsOrders";
import { PriceChart } from "./components/PriceChart";
import { StrategyRiskPanel } from "./components/StrategyRiskPanel";
import type { BrokerMode, DashboardSnapshot, NewsCheckResult, PriceBar } from "./types";

const DEFAULT_UNIVERSE = [
  "SPY",
  "QQQ",
  "IWM",
  "TLT",
  "GLD",
  "AAPL",
  "MSFT",
  "NVDA",
  "AMZN",
  "META",
  "AMD",
];

function fmtMoney(val: number | undefined): string {
  if (val === undefined) return "—";
  const sign = val < 0 ? "-" : "";
  return `${sign}$${Math.abs(val).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function App() {
  const [view, setView] = useState<"terminal" | "bots" | "backtest">("terminal");
  const [universe, setUniverse] = useState<string[]>(DEFAULT_UNIVERSE);
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState("SPY");
  const [bars, setBars] = useState<PriceBar[]>([]);
  const [newsCheck, setNewsCheck] = useState<NewsCheckResult | null>(null);
  const [newsRefreshing, setNewsRefreshing] = useState(false);
  const [newSymbol, setNewSymbol] = useState("");
  const [wsLive, setWsLive] = useState(false);
  const [connError, setConnError] = useState<string | null>(null);
  const [showBrokerConfig, setShowBrokerConfig] = useState(false);

  const [ibHost, setIbHost] = useState("127.0.0.1");
  const [ibPort, setIbPort] = useState("7497");
  const [ibClientId, setIbClientId] = useState("1");

  const wsRef = useRef<WebSocket | null>(null);

  const loadSnapshot = useCallback(async () => {
    try {
      const data = await api.getSnapshot();
      setSnapshot(data);
    } catch {
      // backend may still be starting
    }
  }, []);

  const loadHistory = useCallback(async (sym: string) => {
    try {
      const data = await api.getHistory(sym, 60);
      setBars(data);
    } catch {
      setBars([]);
    }
  }, []);

  useEffect(() => {
    void loadSnapshot();
    void api.getUniverse().then((u) => {
      if (u.symbols.length > 0) setUniverse(u.symbols);
    }).catch(() => {});
  }, [loadSnapshot]);

  useEffect(() => {
    void loadHistory(selectedSymbol);
    void api.getNews(selectedSymbol).then(setNewsCheck).catch(() => setNewsCheck(null));
    const id = window.setInterval(() => {
      void loadHistory(selectedSymbol);
    }, 3000);
    return () => window.clearInterval(id);
  }, [selectedSymbol, loadHistory]);

  useEffect(() => {
    let active = true;
    let retryTimer: number | undefined;

    function connectWs() {
      if (!active) return;
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${proto}//${window.location.host}/ws`);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!active) {
          ws.close();
          return;
        }
        setWsLive(true);
      };
      ws.onmessage = (event) => {
        if (!active) return;
        try {
          const data = JSON.parse(event.data as string) as DashboardSnapshot;
          setSnapshot(data);
        } catch {
          // ignore malformed payload
        }
      };
      ws.onclose = () => {
        if (!active) return;
        setWsLive(false);
        retryTimer = window.setTimeout(connectWs, 2000);
      };
    }

    connectWs();
    return () => {
      active = false;
      if (retryTimer) window.clearTimeout(retryTimer);
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.close();
      }
    };
  }, []);

  const autoConnectAttemptedRef = useRef(false);

  useEffect(() => {
    if (!snapshot || autoConnectAttemptedRef.current) return;
    autoConnectAttemptedRef.current = true;
    if (snapshot.connection.connected) return;
    const raw = localStorage.getItem("ibkr_trader_autoconnect");
    if (!raw) return;
    try {
      const saved = JSON.parse(raw) as {
        mode: BrokerMode;
        host?: string;
        port?: number;
        clientId?: number;
      };
      if (!saved?.mode) return;
      (async () => {
        try {
          if (saved.mode === "ibkr") {
            const h = saved.host || "127.0.0.1";
            const p = saved.port || 7497;
            const cid = saved.clientId || 1;
            setIbHost(h);
            setIbPort(String(p));
            setIbClientId(String(cid));
            await api.switchMode("ibkr", h, p, cid);
          } else if (snapshot.connection.broker_mode !== "simulator") {
            await api.switchMode("simulator", "127.0.0.1", 7497, 1);
          }
          await api.connect();
          await loadSnapshot();
        } catch (err) {
          setConnError(
            err instanceof Error ? `Auto-connect failed: ${err.message}` : "Auto-connect failed"
          );
          await loadSnapshot();
        }
      })();
    } catch {
      localStorage.removeItem("ibkr_trader_autoconnect");
    }
  }, [snapshot, loadSnapshot]);

  async function handleConnectToggle() {
    if (!snapshot) return;
    setConnError(null);
    try {
      if (snapshot.connection.connected) {
        localStorage.removeItem("ibkr_trader_autoconnect");
        await api.disconnect();
      } else {
        await api.connect();
        localStorage.setItem(
          "ibkr_trader_autoconnect",
          JSON.stringify({
            mode: snapshot.connection.broker_mode,
            host: ibHost.trim() || "127.0.0.1",
            port: Number(ibPort) || 7497,
            clientId: Number(ibClientId) || 1,
          })
        );
      }
      await loadSnapshot();
    } catch (err) {
      setConnError(err instanceof Error ? err.message : "Connection failed");
    }
  }

  async function handleModeSwitch(mode: BrokerMode) {
    setConnError(null);
    const host = ibHost.trim() || "127.0.0.1";
    const port = Number(ibPort) || 7497;
    const clientId = Number(ibClientId) || 1;
    try {
      await api.switchMode(mode, host, port, clientId);
      if (mode === "ibkr") {
        await api.connect();
        localStorage.setItem(
          "ibkr_trader_autoconnect",
          JSON.stringify({ mode: "ibkr", host, port, clientId })
        );
      } else {
        localStorage.removeItem("ibkr_trader_autoconnect");
      }
      setShowBrokerConfig(false);
      await loadSnapshot();
    } catch (err) {
      setConnError(err instanceof Error ? err.message : "Failed to switch broker");
      await loadSnapshot();
    }
  }

  async function handleKillSwitch() {
    if (!snapshot) return;
    const nextHalted = !snapshot.risk.trading_halted;
    await api.setKillSwitch(
      nextHalted,
      true,
      nextHalted ? "Operator kill switch" : undefined
    );
    await loadSnapshot();
  }

  async function handleRefreshNews() {
    setNewsRefreshing(true);
    try {
      const fresh = await api.getNews(selectedSymbol, true);
      setNewsCheck(fresh);
    } catch {
      // keep existing card state
    } finally {
      setNewsRefreshing(false);
    }
  }

  async function handleAddWatchlist(e: React.FormEvent) {
    e.preventDefault();
    const sym = newSymbol.trim().toUpperCase();
    if (!sym) return;
    await api.addWatchlist(sym);
    setNewSymbol("");
    setSelectedSymbol(sym);
    await loadSnapshot();
  }

  const conn = snapshot?.connection;
  const market = snapshot?.market;
  const account = snapshot?.account;
  const risk = snapshot?.risk;
  const quotes = snapshot?.quotes ?? [];
  const selectedQuote = quotes.find((q) => q.symbol === selectedSymbol);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <TrendingUp size={20} className="brand-icon" />
          <span className="brand-name">IBKR Trader</span>
          {conn && (
            <span className={`env-badge env-${conn.environment.toLowerCase()}`}>
              {conn.environment}
            </span>
          )}
          {market && (
            <span
              className={`mkt-badge ${market.is_rth_open ? "mkt-open" : "mkt-closed"}`}
              title={`${market.reason} (${market.et_time})`}
            >
              US {market.session} · {market.et_time.slice(11, 16)} ET
            </span>
          )}
          <span className={`ws-indicator ${wsLive ? "online" : "offline"}`} title="WebSocket Stream">
            {wsLive ? <Wifi size={14} /> : <WifiOff size={14} />}
            {wsLive ? "LIVE STREAM" : "RECONNECTING"}
          </span>
        </div>

        <div className="topbar-actions">
          <div className="nav-tabs">
            <button
              className={`nav-tab ${view === "terminal" ? "active" : ""}`}
              onClick={() => setView("terminal")}
            >
              <LayoutDashboard size={14} />
              Live Terminal
            </button>
            <button
              className={`nav-tab ${view === "bots" ? "active" : ""}`}
              onClick={() => setView("bots")}
            >
              <Bot size={14} />
              Bot Fleet
            </button>
            <button
              className={`nav-tab ${view === "backtest" ? "active" : ""}`}
              onClick={() => setView("backtest")}
            >
              <FlaskConical size={14} />
              Backtest Lab
            </button>
          </div>

          <button
            className="btn-outline"
            onClick={() => setShowBrokerConfig((v) => !v)}
          >
            <Plug size={15} />
            {conn?.broker_mode === "ibkr"
              ? `IBKR (${conn.host}:${conn.port})`
              : "Simulator Mode"}
          </button>

          <button
            className={`btn-outline ${conn?.connected ? "btn-connected" : ""}`}
            onClick={handleConnectToggle}
          >
            <Activity size={15} />
            {conn?.connected ? "Disconnect" : "Connect"}
          </button>

          {risk && (
            <button
              className={`kill-btn ${risk.trading_halted ? "halted" : ""}`}
              onClick={handleKillSwitch}
            >
              <AlertOctagon size={15} />
              {risk.trading_halted ? "RESUME TRADING" : "KILL SWITCH"}
            </button>
          )}
        </div>
      </header>

      {connError && (
        <div className="banner-error">
          <ShieldAlert size={16} />
          <span>{connError}</span>
          <button className="icon-close" onClick={() => setConnError(null)}>
            <X size={14} />
          </button>
        </div>
      )}

      {conn?.broker_mode === "simulator" && (
        <div className="banner-sim">
          <span>
            SIMULATOR MODE: Watchlist and chart prices are synthetic in-memory data, not IBKR quotes.
          </span>
          <button
            className="mini-btn"
            onClick={() => setShowBrokerConfig(true)}
          >
            Connect IBKR Paper
          </button>
        </div>
      )}

      {risk?.trading_halted && (
        <div className="banner-halt">
          <AlertOctagon size={16} />
          <span>
            TRADING HALTED: {risk.halt_reason || "Kill switch engaged"} — New orders are blocked.
          </span>
        </div>
      )}

      {showBrokerConfig && (
        <div className="broker-drawer">
          <div className="broker-drawer-inner">
            <div>
              <strong>Broker Connection Mode</strong>
              <p className="muted-sm">
                Use the built-in Simulator anytime, or connect to local TWS / IB Gateway
                (Paper: 7497 / 4002, Live: 7496 / 4001).
              </p>
            </div>
            <div className="broker-controls">
              <button
                className={`btn-outline ${conn?.broker_mode === "simulator" ? "active-choice" : ""}`}
                onClick={() => handleModeSwitch("simulator")}
              >
                Use Paper Simulator
              </button>
              <div className="ibkr-inputs">
                <input
                  value={ibHost}
                  onChange={(e) => setIbHost(e.target.value)}
                  placeholder="Host (127.0.0.1)"
                  title="TWS/Gateway Host"
                />
                <input
                  value={ibPort}
                  onChange={(e) => setIbPort(e.target.value)}
                  placeholder="Port (7497)"
                  title="7497 TWS Paper, 4002 Gateway Paper"
                />
                <input
                  value={ibClientId}
                  onChange={(e) => setIbClientId(e.target.value)}
                  placeholder="Client ID"
                  title="Client ID"
                />
                <button
                  className="btn-primary-sm"
                  onClick={() => handleModeSwitch("ibkr")}
                >
                  Connect IBKR
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <section className="kpi-row">
        <div className="kpi-card">
          <span className="kpi-label">Net Liquidation</span>
          <strong className="kpi-value">{fmtMoney(account?.net_liquidation)}</strong>
          <span className="muted-sm">{account?.account_id ?? "Disconnected"}</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label">Cash Balance</span>
          <strong className="kpi-value">{fmtMoney(account?.cash)}</strong>
          <span className="muted-sm">{account?.currency ?? "USD"}</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label">Buying Power</span>
          <strong className="kpi-value">{fmtMoney(account?.buying_power)}</strong>
          <span className="muted-sm">Available margin</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label">Unrealized P&amp;L</span>
          <strong
            className={`kpi-value ${(account?.unrealized_pnl ?? 0) >= 0 ? "pos" : "neg"}`}
          >
            {fmtMoney(account?.unrealized_pnl)}
          </strong>
          <span className="muted-sm">Open positions</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-label">Daily / Realized P&amp;L</span>
          <strong
            className={`kpi-value ${(account?.daily_pnl ?? 0) >= 0 ? "pos" : "neg"}`}
          >
            {fmtMoney(account?.daily_pnl)}
          </strong>
          <span className="muted-sm">
            Realized: {fmtMoney(account?.realized_pnl)}
          </span>
        </div>
      </section>

      {view === "bots" ? (
        <BotFleetPanel
          bots={snapshot?.strategies ?? []}
          universe={universe}
          onRefresh={loadSnapshot}
        />
      ) : view === "backtest" ? (
        <BacktestLab
          universe={universe}
          ibkrConnected={Boolean(conn?.broker_mode === "ibkr" && conn.connected)}
        />
      ) : (
        <main className="main-grid">
          <aside className="watchlist-col card">
            <div className="card-header">
              <h3>Watchlist</h3>
              <span className="muted-sm">{quotes.length} symbols</span>
            </div>

            <form className="watch-add" onSubmit={handleAddWatchlist}>
              <input
                value={newSymbol}
                onChange={(e) => setNewSymbol(e.target.value.toUpperCase())}
                placeholder="Add ticker (e.g. AMD)"
              />
              <button type="submit" title="Add symbol">
                <Plus size={15} />
              </button>
            </form>

            <div className="watch-list">
              {quotes.map((q) => (
                <div
                  key={q.symbol}
                  className={`watch-row ${q.symbol === selectedSymbol ? "selected" : ""}`}
                  onClick={() => setSelectedSymbol(q.symbol)}
                >
                  <div>
                    <div className="watch-sym">{q.symbol}</div>
                    <div className="muted-xs">
                      {q.bid.toFixed(2)} / {q.ask.toFixed(2)}
                    </div>
                  </div>
                  <div className="watch-right">
                    <div className="watch-price">${q.last.toFixed(2)}</div>
                    <div className={`watch-chg ${q.change_pct >= 0 ? "pos" : "neg"}`}>
                      {q.change_pct >= 0 ? "+" : ""}
                      {q.change_pct.toFixed(2)}%
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </aside>

          <section className="center-col">
            <PriceChart symbol={selectedSymbol} bars={bars} quote={selectedQuote} />
            <PositionsOrders
              positions={snapshot?.positions ?? []}
              orders={snapshot?.orders ?? []}
              onSelectSymbol={setSelectedSymbol}
              onRefresh={loadSnapshot}
            />
            {newsCheck && (
              <div className="card">
                <div className="card-header">
                  <h3>
                    <Newspaper size={15} style={{ verticalAlign: "text-bottom", marginRight: 6 }} />
                    News &amp; Catalyst Guard — {newsCheck.symbol}
                  </h3>
                  <div className="news-badges">
                    <span
                      className="pill news-source-pill"
                      title={newsCheck.model ? `Model: ${newsCheck.model}` : "Keyword & calendar heuristic"}
                    >
                      {newsCheck.analysis_source === "gemini" ? (
                        <>
                          <Sparkles size={11} />
                          {newsCheck.model || "Gemini"}
                        </>
                      ) : (
                        "Heuristic"
                      )}
                      {newsCheck.confidence != null
                        ? ` · ${Math.round(newsCheck.confidence * 100)}%`
                        : ""}
                    </span>
                    {newsCheck.classification && (
                      <span className="pill news-class-pill">
                        {newsCheck.classification.replaceAll("_", " ")}
                      </span>
                    )}
                    <span className={`pill ${newsCheck.blocked ? "pill-sell" : "pill-buy"}`}>
                      {newsCheck.blocked ? "VETO ACTIVE" : "CLEAR"}
                    </span>
                    <button
                      type="button"
                      className="mini-btn"
                      onClick={handleRefreshNews}
                      disabled={newsRefreshing}
                      title="Re-analyze headlines"
                    >
                      <RefreshCw size={12} className={newsRefreshing ? "spin" : ""} />
                    </button>
                  </div>
                </div>
                <div className="news-rationale">
                  {newsCheck.rationale || newsCheck.reason}
                  {newsCheck.earnings_date ? ` · Earnings: ${newsCheck.earnings_date}` : ""}
                </div>
                {newsCheck.headlines.length > 0 && (
                  <div className="news-list">
                    {newsCheck.headlines.slice(0, 4).map((h, idx) => (
                      <div key={idx} className="news-item">
                        <span className="news-title">{h.title}</span>
                        <span className="muted-xs">
                          {h.publisher} {h.published_at ? `· ${h.published_at}` : ""}
                          {h.catalyst_match ? ` · [${h.catalyst_match}]` : ""}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </section>

          <aside className="right-col">
            <OrderTicket
              selectedSymbol={selectedSymbol}
              quote={selectedQuote}
              halted={risk?.trading_halted ?? false}
              connected={conn?.connected ?? false}
              onOrderPlaced={loadSnapshot}
            />
            {snapshot && (
              <StrategyRiskPanel
                strategies={snapshot.strategies}
                risk={snapshot.risk}
                activity={snapshot.activity}
                onRefresh={loadSnapshot}
                onOpenFleet={() => setView("bots")}
              />
            )}
          </aside>
        </main>
      )}
    </div>
  );
}

export default App;

