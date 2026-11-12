import { useEffect, useState } from "react";
import { api } from "../api";
import type { OrderSide, OrderType, Quote, TimeInForce } from "../types";

interface OrderTicketProps {
  selectedSymbol: string;
  quote?: Quote;
  halted: boolean;
  connected: boolean;
  onOrderPlaced: () => void;
}

export function OrderTicket({
  selectedSymbol,
  quote,
  halted,
  connected,
  onOrderPlaced,
}: OrderTicketProps) {
  const [symbol, setSymbol] = useState(selectedSymbol);
  const [side, setSide] = useState<OrderSide>("BUY");
  const [orderType, setOrderType] = useState<OrderType>("MKT");
  const [quantity, setQuantity] = useState("10");
  const [limitPrice, setLimitPrice] = useState("");
  const [stopPrice, setStopPrice] = useState("");
  const [stopLossPrice, setStopLossPrice] = useState("");
  const [takeProfitPrice, setTakeProfitPrice] = useState("");
  const [tif, setTif] = useState<TimeInForce>("DAY");
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<{ kind: "ok" | "err"; text: string } | null>(
    null
  );

  useEffect(() => {
    setSymbol(selectedSymbol);
    if (quote) {
      setLimitPrice(quote.last.toFixed(2));
      setStopPrice(quote.last.toFixed(2));
    }
  }, [selectedSymbol]);

  const qtyNum = Math.max(0, Number.parseInt(quantity || "0", 10) || 0);
  const estPrice =
    orderType === "LMT"
      ? Number.parseFloat(limitPrice) || quote?.last || 0
      : orderType === "STP"
        ? Number.parseFloat(stopPrice) || quote?.last || 0
        : quote?.last || 0;
  const estNotional = qtyNum * estPrice;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFeedback(null);
    setSubmitting(true);
    try {
      const order = await api.placeOrder({
        symbol: symbol.trim().toUpperCase(),
        side,
        quantity: qtyNum,
        order_type: orderType,
        limit_price: orderType === "LMT" ? Number.parseFloat(limitPrice) : undefined,
        stop_price: orderType === "STP" ? Number.parseFloat(stopPrice) : undefined,
        stop_loss_price: stopLossPrice ? Number.parseFloat(stopLossPrice) : undefined,
        take_profit_price: takeProfitPrice ? Number.parseFloat(takeProfitPrice) : undefined,
        tif,
      });
      setFeedback({
        kind: "ok",
        text: `#${order.order_id} ${order.side} ${order.quantity} ${order.symbol} → ${order.status}`,
      });
      onOrderPlaced();
    } catch (err) {
      setFeedback({
        kind: "err",
        text: err instanceof Error ? err.message : "Order failed",
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="card">
      <div className="card-header">
        <h3>Order Ticket</h3>
        {quote && <span className="muted-sm">Last ${quote.last.toFixed(2)}</span>}
      </div>

      <form className="order-form" onSubmit={handleSubmit}>
        <div className="side-toggle">
          <button
            type="button"
            className={`side-btn buy ${side === "BUY" ? "active" : ""}`}
            onClick={() => setSide("BUY")}
          >
            BUY
          </button>
          <button
            type="button"
            className={`side-btn sell ${side === "SELL" ? "active" : ""}`}
            onClick={() => setSide("SELL")}
          >
            SELL
          </button>
        </div>

        <div className="form-grid-2">
          <label className="field">
            <span>Symbol</span>
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              placeholder="AAPL"
              required
            />
          </label>
          <label className="field">
            <span>Quantity</span>
            <input
              type="number"
              min={1}
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              required
            />
          </label>
        </div>

        <div className="form-grid-2">
          <label className="field">
            <span>Order Type</span>
            <select
              value={orderType}
              onChange={(e) => setOrderType(e.target.value as OrderType)}
            >
              <option value="MKT">Market (MKT)</option>
              <option value="LMT">Limit (LMT)</option>
              <option value="STP">Stop (STP)</option>
            </select>
          </label>
          <label className="field">
            <span>Time in Force</span>
            <select value={tif} onChange={(e) => setTif(e.target.value as TimeInForce)}>
              <option value="DAY">DAY</option>
              <option value="GTC">GTC</option>
            </select>
          </label>
        </div>

        {orderType === "LMT" && (
          <label className="field">
            <span>Limit Price ($)</span>
            <input
              type="number"
              step="0.01"
              min="0.01"
              value={limitPrice}
              onChange={(e) => setLimitPrice(e.target.value)}
              required
            />
          </label>
        )}

        {orderType === "STP" && (
          <label className="field">
            <span>Stop Price ($)</span>
            <input
              type="number"
              step="0.01"
              min="0.01"
              value={stopPrice}
              onChange={(e) => setStopPrice(e.target.value)}
              required
            />
          </label>
        )}

        <div className="form-grid-2">
          <label className="field">
            <span>Stop-Loss ($) (opt)</span>
            <input
              type="number"
              step="0.01"
              min="0.01"
              placeholder="Server-side SL"
              value={stopLossPrice}
              onChange={(e) => setStopLossPrice(e.target.value)}
            />
          </label>
          <label className="field">
            <span>Take-Profit ($) (opt)</span>
            <input
              type="number"
              step="0.01"
              min="0.01"
              placeholder="Server-side TP"
              value={takeProfitPrice}
              onChange={(e) => setTakeProfitPrice(e.target.value)}
            />
          </label>
        </div>

        <div className="notional-row">
          <span>Est. Notional</span>
          <strong>
            ${estNotional.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </strong>
        </div>

        <button
          type="submit"
          disabled={submitting || halted || !connected || qtyNum <= 0}
          className={`submit-order-btn ${side === "BUY" ? "buy" : "sell"}`}
        >
          {halted
            ? "Trading Halted"
            : !connected
              ? "Broker Disconnected"
              : submitting
                ? "Routing..."
                : `Submit ${side} ${qtyNum} ${symbol}`}
        </button>

        {feedback && (
          <div className={`toast ${feedback.kind === "ok" ? "toast-ok" : "toast-err"}`}>
            {feedback.text}
          </div>
        )}
      </form>
    </div>
  );
}

