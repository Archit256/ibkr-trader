"""Historical OHLCV bar downloader (yfinance & IBKR) backed by SQLite cache."""

from __future__ import annotations

import asyncio
from typing import Any

from app.broker.base import BrokerError
from app.broker.ibkr_client import IBKRClient
from app.models import DataSource, PriceBar, Timeframe
from app.storage.db import TradingStore

YF_INTERVAL_MAP: dict[Timeframe, str] = {
    "5 mins": "5m",
    "15 mins": "15m",
    "1 hour": "60m",
    "1 day": "1d",
}

YF_DEFAULT_PERIOD: dict[Timeframe, str] = {
    "5 mins": "60d",
    "15 mins": "60d",
    "1 hour": "730d",
    "1 day": "20y",
}

IBKR_DEFAULT_DURATION: dict[Timeframe, str] = {
    "5 mins": "30 D",
    "15 mins": "60 D",
    "1 hour": "180 D",
    "1 day": "10 Y",
}


def _fetch_yfinance_sync(symbol: str, timeframe: Timeframe, period: str) -> list[PriceBar]:
    import yfinance as yf  # local import so core server starts even before optional import

    interval = YF_INTERVAL_MAP[timeframe]
    effective_period = period or YF_DEFAULT_PERIOD[timeframe]
    # Yahoo limits 5m/15m history to 60d
    if timeframe in ("5 mins", "15 mins") and effective_period.endswith("y"):
        effective_period = "60d"

    ticker = yf.Ticker(symbol)
    df = ticker.history(period=effective_period, interval=interval, auto_adjust=True)
    if df is None or df.empty:
        return []

    bars: list[PriceBar] = []
    for idx, row in df.iterrows():
        ts_str = (
            idx.strftime("%Y-%m-%d")
            if timeframe == "1 day"
            else idx.strftime("%Y-%m-%d %H:%M")
        )
        o = float(row["Open"])
        h = float(row["High"])
        l = float(row["Low"])
        c = float(row["Close"])
        v = int(row["Volume"] or 0)
        if any(x != x for x in (o, h, l, c)):  # skip NaN rows
            continue
        bars.append(
            PriceBar(
                time=ts_str,
                open=round(o, 2),
                high=round(h, 2),
                low=round(l, 2),
                close=round(c, 2),
                volume=max(0, v),
            )
        )
    return bars


class HistoricalDataService:
    """Fetches and caches US stock/ETF bars from IBKR or Yahoo Finance."""

    def __init__(self, store: TradingStore) -> None:
        self._store = store

    async def sync_symbol(
        self,
        symbol: str,
        timeframe: Timeframe,
        source: DataSource = "yfinance",
        lookback: str = "",
        ibkr_client: IBKRClient | None = None,
    ) -> int:
        sym = symbol.strip().upper()
        bars: list[PriceBar]
        if source == "ibkr":
            if ibkr_client is None or not ibkr_client.status().connected:
                raise BrokerError("IBKR must be connected to sync historical bars from IBKR.")
            duration = lookback if (" " in lookback) else IBKR_DEFAULT_DURATION[timeframe]
            bars = await ibkr_client.fetch_historical_bars(
                symbol=sym,
                duration_str=duration,
                bar_size=timeframe,
                use_rth=True,
            )
        else:
            bars = await asyncio.to_thread(
                _fetch_yfinance_sync, sym, timeframe, lookback or YF_DEFAULT_PERIOD[timeframe]
            )

        if not bars:
            return 0
        return self._store.upsert_historical_bars(sym, timeframe, bars, source)

    def get_cached_bars(
        self, symbol: str, timeframe: Timeframe, limit: int = 20000
    ) -> list[PriceBar]:
        return self._store.load_historical_bars(symbol, timeframe, limit=limit)

