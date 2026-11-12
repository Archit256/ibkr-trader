import { useState } from "react";
import { api } from "../api";
import type { OrderRecord, Position } from "../types";

interface PositionsOrdersProps {
  positions: Position[];
  orders: OrderRecord[];
  onSelectSymbol: (symbol: string) => void;
  onRefresh: () => void;
}

export function PositionsOrders({
  positions,
  orders,
  onSelectSymbol,
  onRefresh,
}: PositionsOrdersProps) {
  const [tab, setTab] = useState<"positions" | "orders">("positions");
  const [busyId, setBusyId] = useState<string | number | null>(null);

  async function closePosition(pos: Position) {
    setBusyId(pos.symbol);
    try {
      await api.placeOrder({
        symbol: pos.symbol,
        side: pos.quantity > 0 ? "SELL" : "BUY",
        quantity: Math.abs(Math.round(pos.quantity)),
        order_type: "MKT",
        tif: "DAY",
      });
      onRefresh();
    } finally {
      setBusyId(null);
    }
  }

  async function cancelOrder(orderId: number) {
    setBusyId(orderId);
    try {
      await api.cancelOrder(orderId);
      onRefresh();
    } finally {
      setBusyId(null);
    }
  }

  const openOrdersCount = orders.filter(
    (o) => o.status === "Submitted" || o.status === "PendingSubmit"
  ).length;

  return (
    <div className="card">
      <div className="card-header">
        <div className="tabs">
          <button
            className={`tab-btn ${tab === "positions" ? "active" : ""}`}
            onClick={() => setTab("positions")}
          >
            Positions ({positions.length})
          </button>
          <button
            className={`tab-btn ${tab === "orders" ? "active" : ""}`}
            onClick={() => setTab("orders")}
          >
            Orders ({orders.length}
            {openOrdersCount > 0 ? ` · ${openOrdersCount} open` : ""})
          </button>
        </div>
      </div>

      {tab === "positions" ? (
        positions.length === 0 ? (
          <div className="empty-box">No open positions. Submit an order to open a position.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th className="num">Qty</th>
                  <th className="num">Avg Cost</th>
                  <th className="num">Last</th>
                  <th className="num">Mkt Value</th>
                  <th className="num">Unrealized P&amp;L</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {positions.map((pos) => (
                  <tr key={pos.symbol}>
                    <td>
                      <button
                        className="link-sym"
                        onClick={() => onSelectSymbol(pos.symbol)}
                      >
                        {pos.symbol}
                      </button>
                    </td>
                    <td className="num">{pos.quantity}</td>
                    <td className="num">${pos.avg_cost.toFixed(2)}</td>
                    <td className="num">${pos.market_price.toFixed(2)}</td>
                    <td className="num">${pos.market_value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                    <td className={`num ${pos.unrealized_pnl >= 0 ? "pos" : "neg"}`}>
                      {pos.unrealized_pnl >= 0 ? "+" : ""}$
                      {pos.unrealized_pnl.toFixed(2)}
                    </td>
                    <td className="num">
                      <button
                        className="mini-btn danger"
                        disabled={busyId === pos.symbol}
                        onClick={() => closePosition(pos)}
                      >
                        {busyId === pos.symbol ? "..." : "Close"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : orders.length === 0 ? (
        <div className="empty-box">No orders placed yet this session.</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Symbol</th>
                <th>Side</th>
                <th>Type</th>
                <th className="num">Qty</th>
                <th className="num">Price</th>
                <th>Status</th>
                <th>Source</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {orders.map((ord) => {
                const isOpen =
                  ord.status === "Submitted" || ord.status === "PendingSubmit";
                const displayPrice =
                  ord.avg_fill_price ?? ord.limit_price ?? ord.stop_price;
                return (
                  <tr key={ord.order_id}>
                    <td className="muted-sm">#{ord.order_id}</td>
                    <td>
                      <button
                        className="link-sym"
                        onClick={() => onSelectSymbol(ord.symbol)}
                      >
                        {ord.symbol}
                      </button>
                    </td>
                    <td>
                      <span className={`pill ${ord.side === "BUY" ? "pill-buy" : "pill-sell"}`}>
                        {ord.side}
                      </span>
                    </td>
                    <td>{ord.order_type}</td>
                    <td className="num">
                      {ord.filled_quantity}/{ord.quantity}
                    </td>
                    <td className="num">
                      {displayPrice !== null ? `$${displayPrice.toFixed(2)}` : "MKT"}
                    </td>
                    <td>
                      <span className={`status-tag status-${ord.status.toLowerCase()}`}>
                        {ord.status}
                      </span>
                    </td>
                    <td className="muted-sm">{ord.source}</td>
                    <td className="num">
                      {isOpen && (
                        <button
                          className="mini-btn"
                          disabled={busyId === ord.order_id}
                          onClick={() => cancelOrder(ord.order_id)}
                        >
                          Cancel
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

