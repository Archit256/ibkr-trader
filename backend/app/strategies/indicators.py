"""Pure technical indicator and signal helpers shared between live bots and backtesting."""

from __future__ import annotations

from app.models import PriceBar


def sma(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    k = 2.0 / (period + 1)
    val = sum(values[:period]) / period
    for price in values[period:]:
        val = price * k + val * (1.0 - k)
    return val


def atr(bars: list[PriceBar], period: int = 14) -> float | None:
    """Average True Range over the latest `period` transitions."""
    if period <= 0 or len(bars) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(bars)):
        cur = bars[i]
        prev_close = bars[i - 1].close
        tr = max(
            cur.high - cur.low,
            abs(cur.high - prev_close),
            abs(cur.low - prev_close),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


def wilder_rsi(closes: list[float], period: int = 14) -> float | None:
    """Compute Wilder's RSI on the most recent close."""
    if period <= 0 or len(closes) < period + 1:
        return None

    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rolling_vwap(bars: list[PriceBar], window: int = 24) -> float | None:
    """Volume-weighted average price over the latest `window` bars (or same-day bars if dated)."""
    if not bars:
        return None

    # If bars include YYYY-MM-DD dates, anchor VWAP to the latest session date when available
    last_time = bars[-1].time
    if len(last_time) >= 10 and last_time[4] == "-" and last_time[7] == "-":
        session_date = last_time[:10]
        same_day = [b for b in bars if b.time.startswith(session_date)]
        subset = same_day if len(same_day) >= 3 else bars[-window:]
    else:
        subset = bars[-window:]

    total_pv = 0.0
    total_vol = 0
    for b in subset:
        typical = (b.high + b.low + b.close) / 3.0
        vol = max(b.volume, 1)
        total_pv += typical * vol
        total_vol += vol

    return (total_pv / total_vol) if total_vol > 0 else subset[-1].close

