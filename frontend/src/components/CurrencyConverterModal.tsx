import { useEffect, useState } from "react";
import {
  ArrowDownUp,
  ArrowRight,
  CheckCircle2,
  DollarSign,
  Info,
  RefreshCw,
  X,
} from "lucide-react";
import { api } from "../api";
import type {
  AccountSummary,
  CurrencyConversionResult,
  FxRateResponse,
} from "../types";

interface CurrencyConverterModalProps {
  isOpen: boolean;
  onClose: () => void;
  account: AccountSummary | null;
  connected: boolean;
  onConverted: () => void;
}

export function CurrencyConverterModal({
  isOpen,
  onClose,
  account,
  connected,
  onConverted,
}: CurrencyConverterModalProps) {
  const [direction, setDirection] = useState<"SGD_TO_USD" | "USD_TO_SGD">("SGD_TO_USD");
  const [amount, setAmount] = useState<string>("5000");
  const [amountCurrency, setAmountCurrency] = useState<"SGD" | "USD">("SGD");
  const [orderType, setOrderType] = useState<"MKT" | "LMT">("MKT");
  const [limitRate, setLimitRate] = useState<string>("");
  const [exchange, setExchange] = useState<"IDEALPRO" | "FXCONV">("IDEALPRO");

  const [rateData, setRateData] = useState<FxRateResponse | null>(null);
  const [loadingRate, setLoadingRate] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CurrencyConversionResult | null>(null);

  const sgdBalance = account?.currency_balances?.SGD ?? 0;
  const usdBalance = account?.currency_balances?.USD ?? account?.cash ?? 0;

  // Fetch FX Rate
  async function fetchRate() {
    setLoadingRate(true);
    try {
      const data = await api.getFxRate("USDSGD");
      setRateData(data);
      if (!limitRate) {
        setLimitRate(data.rate.toFixed(4));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch USD/SGD exchange rate");
    } finally {
      setLoadingRate(false);
    }
  }

  useEffect(() => {
    if (isOpen) {
      void fetchRate();
      setError(null);
      setResult(null);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const currentRate = rateData?.rate ?? 1.2870;
  const numAmount = Number.parseFloat(amount) || 0;

  // Calculate estimated proceeds
  let fromAmount = 0;
  let toAmount = 0;
  let fromCurr = "SGD";
  let toCurr = "USD";

  if (direction === "SGD_TO_USD") {
    fromCurr = "SGD";
    toCurr = "USD";
    if (amountCurrency === "SGD") {
      fromAmount = numAmount;
      toAmount = currentRate > 0 ? numAmount / currentRate : 0;
    } else {
      toAmount = numAmount;
      fromAmount = numAmount * currentRate;
    }
  } else {
    fromCurr = "USD";
    toCurr = "SGD";
    if (amountCurrency === "USD") {
      fromAmount = numAmount;
      toAmount = numAmount * currentRate;
    } else {
      toAmount = numAmount;
      fromAmount = currentRate > 0 ? numAmount / currentRate : 0;
    }
  }

  const availableSourceBalance = direction === "SGD_TO_USD" ? sgdBalance : usdBalance;
  const exceedsBalance = fromAmount > availableSourceBalance;
  const remainingSourceBalance = Math.max(0, availableSourceBalance - fromAmount);

  function applyPreset(pct: number) {
    const total = direction === "SGD_TO_USD" ? sgdBalance : usdBalance;
    const calc = Math.floor(total * pct * 100) / 100;
    setAmount(calc > 0 ? String(calc) : "0");
    setAmountCurrency(direction === "SGD_TO_USD" ? "SGD" : "USD");
    setError(null);
  }

  function handleSwapDirection() {
    setDirection((prev) => (prev === "SGD_TO_USD" ? "USD_TO_SGD" : "SGD_TO_USD"));
    setAmountCurrency((prev) => (prev === "SGD" ? "USD" : "SGD"));
    setError(null);
    setResult(null);
  }

  async function handleConvert(e: React.FormEvent) {
    e.preventDefault();
    if (!connected) {
      setError("Broker must be connected before converting currency.");
      return;
    }
    if (fromAmount <= 0) {
      setError("Please specify a valid conversion amount greater than 0.");
      return;
    }
    if (exceedsBalance) {
      setError(
        `Insufficient ${fromCurr} cash balance: you need ${fromAmount.toLocaleString(undefined, { minimumFractionDigits: 2 })} ${fromCurr}, but only have ${availableSourceBalance.toLocaleString(undefined, { minimumFractionDigits: 2 })} ${fromCurr}.`
      );
      return;
    }

    setSubmitting(true);
    setError(null);
    setResult(null);

    try {
      const res = await api.convertCurrency({
        from_currency: fromCurr,
        to_currency: toCurr,
        amount: numAmount,
        amount_currency: amountCurrency,
        order_type: orderType,
        limit_price: orderType === "LMT" ? Number.parseFloat(limitRate) : undefined,
        exchange,
      });
      setResult(res);
      onConverted();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Currency conversion failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content fx-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="fx-title-wrap">
            <DollarSign className="fx-icon" size={22} />
            <div>
              <h3>Convert Cash Balance (SGD ⇄ USD)</h3>
              <p className="modal-subtitle">
                Interactive Brokers IDEALPRO / FXCONV Currency Conversion
              </p>
            </div>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close modal">
            <X size={20} />
          </button>
        </div>

        {/* Live Exchange Rate Bar */}
        <div className="fx-rate-bar">
          <div className="rate-info">
            <span className="rate-label">Live Exchange Rate:</span>
            <span className="rate-val">
              1 USD = <strong>{currentRate.toFixed(4)}</strong> SGD
            </span>
            <span className="rate-inv">
              (1 SGD ≈ {(currentRate > 0 ? 1 / currentRate : 0).toFixed(4)} USD)
            </span>
          </div>
          <button
            type="button"
            className="mini-btn refresh-rate-btn"
            onClick={fetchRate}
            disabled={loadingRate}
            title="Refresh FX quote"
          >
            <RefreshCw size={13} className={loadingRate ? "spin" : ""} />
            {loadingRate ? "Fetching..." : "Refresh"}
          </button>
        </div>

        {/* Available Balances */}
        <div className="fx-balances-row">
          <div className={`fx-balance-pill ${direction === "SGD_TO_USD" ? "source" : "target"}`}>
            <span className="bal-tag">SGD Balance:</span>
            <strong>S${sgdBalance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong>
          </div>
          <div className={`fx-balance-pill ${direction === "USD_TO_SGD" ? "source" : "target"}`}>
            <span className="bal-tag">USD Balance:</span>
            <strong>${usdBalance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong>
          </div>
        </div>

        <form onSubmit={handleConvert} className="fx-form">
          {/* Direction toggle */}
          <div className="fx-direction-box">
            <div className={`dir-card ${direction === "SGD_TO_USD" ? "active" : ""}`}>
              <span className="flag">🇸🇬</span>
              <span className="dir-text">Pay SGD</span>
            </div>
            <button
              type="button"
              className="swap-dir-btn"
              onClick={handleSwapDirection}
              title="Swap conversion direction"
            >
              <ArrowDownUp size={16} />
            </button>
            <div className={`dir-card ${direction === "SGD_TO_USD" ? "active" : ""}`}>
              <span className="flag">🇺🇸</span>
              <span className="dir-text">Receive USD</span>
            </div>
          </div>

          {/* Amount input & presets */}
          <div className="field">
            <div className="field-header">
              <span>Amount to Convert</span>
              <div className="currency-selector">
                <button
                  type="button"
                  className={`chip ${amountCurrency === "SGD" ? "active" : ""}`}
                  onClick={() => setAmountCurrency("SGD")}
                >
                  In SGD
                </button>
                <button
                  type="button"
                  className={`chip ${amountCurrency === "USD" ? "active" : ""}`}
                  onClick={() => setAmountCurrency("USD")}
                >
                  In USD
                </button>
              </div>
            </div>
            <div className="input-group">
              <span className="input-prefix">{amountCurrency === "SGD" ? "S$" : "$"}</span>
              <input
                type="number"
                step="any"
                min="0.01"
                value={amount}
                onChange={(e) => {
                  setAmount(e.target.value);
                  setError(null);
                }}
                placeholder="Enter amount..."
                required
              />
              <span className="input-suffix">{amountCurrency}</span>
            </div>

            {/* Quick Percentage Presets */}
            <div className="preset-buttons">
              <button type="button" className="preset-btn" onClick={() => applyPreset(0.25)}>
                25%
              </button>
              <button type="button" className="preset-btn" onClick={() => applyPreset(0.5)}>
                50%
              </button>
              <button type="button" className="preset-btn" onClick={() => applyPreset(0.75)}>
                75%
              </button>
              <button
                type="button"
                className="preset-btn max"
                onClick={() => applyPreset(1.0)}
              >
                Max ({direction === "SGD_TO_USD" ? "All SGD" : "All USD"})
              </button>
            </div>
          </div>

          {/* Order Settings (Order Type & Exchange) */}
          <div className="form-grid-2">
            <label className="field">
              <span>Order Type</span>
              <select
                value={orderType}
                onChange={(e) => setOrderType(e.target.value as "MKT" | "LMT")}
              >
                <option value="MKT">Market (MKT) · Immediate</option>
                <option value="LMT">Limit (LMT) · Target Rate</option>
              </select>
            </label>

            <label className="field">
              <span>Execution Venue</span>
              <select
                value={exchange}
                onChange={(e) => setExchange(e.target.value as "IDEALPRO" | "FXCONV")}
              >
                <option value="IDEALPRO">IDEALPRO (Standard FX)</option>
                <option value="FXCONV">FXCONV (Currency Conversion)</option>
              </select>
            </label>
          </div>

          {orderType === "LMT" && (
            <label className="field">
              <span>Limit Exchange Rate (USD/SGD)</span>
              <input
                type="number"
                step="0.0001"
                min="0.1"
                value={limitRate}
                onChange={(e) => setLimitRate(e.target.value)}
                placeholder="e.g. 1.2850"
                required
              />
            </label>
          )}

          {/* Conversion Calculation Summary Card */}
          <div className="conversion-summary-card">
            <div className="summary-row">
              <span className="muted-sm">You Pay:</span>
              <strong className="neg">
                {fromAmount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} {fromCurr}
              </strong>
            </div>
            <div className="summary-row">
              <span className="muted-sm">Exchange Rate:</span>
              <span>1 USD = {currentRate.toFixed(4)} SGD</span>
            </div>
            <div className="summary-row highlight">
              <span>Estimated Proceeds:</span>
              <strong className="pos">
                ~{toAmount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} {toCurr}
              </strong>
            </div>
            <div className="summary-row divider">
              <span className="muted-sm">Remaining {fromCurr} Balance:</span>
              <span>
                {remainingSourceBalance.toLocaleString(undefined, {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })}{" "}
                {fromCurr}
              </span>
            </div>
          </div>

          {/* Venue Notice */}
          <div className="venue-note">
            <Info size={14} />
            <span>
              {exchange === "IDEALPRO"
                ? "IDEALPRO routes to IBKR interbank forex liquidity pool."
                : "FXCONV is dedicated for retail multi-currency balance adjustment without opening virtual positions."}
            </span>
          </div>

          {error && <div className="toast toast-err">{error}</div>}

          {result && (
            <div className="toast toast-ok">
              <CheckCircle2 size={16} />
              <div>
                <strong>{result.message}</strong>
                <div className="muted-sm">
                  Order ID #{result.order_id} · Rate: {result.rate.toFixed(4)} · Status: {result.status}
                </div>
              </div>
            </div>
          )}

          <button
            type="submit"
            className="btn-full btn-primary convert-submit-btn"
            disabled={submitting || !connected || fromAmount <= 0 || exceedsBalance}
          >
            {submitting ? (
              "Routing FX Order to IBKR..."
            ) : !connected ? (
              "Broker Disconnected"
            ) : exceedsBalance ? (
              `Insufficient ${fromCurr} Balance`
            ) : (
              <>
                Convert {fromAmount.toLocaleString(undefined, { maximumFractionDigits: 2 })} {fromCurr}{" "}
                <ArrowRight size={16} /> ~{toAmount.toLocaleString(undefined, { maximumFractionDigits: 2 })}{" "}
                {toCurr}
              </>
            )}
          </button>
        </form>
      </div>
    </div>
  );
}
