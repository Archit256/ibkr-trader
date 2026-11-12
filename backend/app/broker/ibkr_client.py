"""Interactive Brokers TWS / IB Gateway client with bracket orders & auto-reconnect."""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from ib_async import IB, LimitOrder, MarketOrder, Order, Stock, StopOrder, Trade

from app.broker.base import BrokerClient, BrokerError
from app.config import LIVE_IBKR_PORTS
from app.models import (
    AccountSummary,
    ConnectionStatus,
    OrderRecord,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    PriceBar,
    Quote,
    TimeInForce,
    Timeframe,
)


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        return default if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return default


def _map_status(ib_status: str) -> OrderStatus:
    mapping: dict[str, OrderStatus] = {
        "PendingSubmit": "PendingSubmit",
        "ApiPending": "PendingSubmit",
        "PreSubmitted": "Submitted",
        "Submitted": "Submitted",
        "Filled": "Filled",
        "Cancelled": "Cancelled",
        "ApiCancelled": "Cancelled",
        "PendingCancel": "Submitted",
        "Inactive": "Rejected",
    }
    return mapping.get(ib_status, "Submitted")


def build_ib_orders(order: OrderRequest, next_order_id: int) -> list[Order]:
    """Construct single or server-side OCA bracket IBKR orders."""
    has_bracket = (
        order.stop_loss_price is not None or order.take_profit_price is not None
    )
    if order.order_type == "MKT":
        parent = MarketOrder(order.side, order.quantity, tif=order.tif)
    elif order.order_type == "LMT":
        assert order.limit_price is not None
        parent = LimitOrder(
            order.side, order.quantity, order.limit_price, tif=order.tif
        )
    else:
        assert order.stop_price is not None
        parent = StopOrder(
            order.side, order.quantity, order.stop_price, tif=order.tif
        )

    parent.orderId = next_order_id
    if not has_bracket:
        parent.transmit = True
        return [parent]

    parent.transmit = False
    children: list[Order] = []
    exit_side = "SELL" if order.side == "BUY" else "BUY"
    child_id = next_order_id + 1

    if order.take_profit_price is not None:
        tp = LimitOrder(exit_side, order.quantity, order.take_profit_price, tif="GTC")
        tp.orderId = child_id
        tp.parentId = parent.orderId
        tp.transmit = False
        children.append(tp)
        child_id += 1

    if order.stop_loss_price is not None:
        sl = StopOrder(exit_side, order.quantity, order.stop_loss_price, tif="GTC")
        sl.orderId = child_id
        sl.parentId = parent.orderId
        sl.transmit = False
        children.append(sl)

    children[-1].transmit = True
    return [parent, *children]


class IBKRClient(BrokerClient):
    """Async adapter over ib_async.IB for TWS and IB Gateway."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        allow_live_trading: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._allow_live_trading = allow_live_trading
        self._ib = IB()
        self._last_error: str | None = None
        self._subscribed_tickers: dict[str, Any] = {}
        self._qualified_contracts: dict[str, Stock] = {}
        self._qualifying_tasks: dict[str, asyncio.Task[Stock]] = {}
        self._bar_cache: dict[tuple[str, str, int], tuple[float, list[PriceBar]]] = {}
        self._want_connected = False
        self._reconnect_attempts = 0
        self._next_reconnect_at = 0.0

    @property
    def is_live(self) -> bool:
        return self._port in LIVE_IBKR_PORTS

    @property
    def should_reconnect(self) -> bool:
        return (
            self._want_connected
            and not self._ib.isConnected()
            and time.monotonic() >= self._next_reconnect_at
        )

    def status(self) -> ConnectionStatus:
        return ConnectionStatus(
            broker_mode="ibkr",
            connected=self._ib.isConnected(),
            environment="LIVE" if self.is_live else "PAPER",
            host=self._host,
            port=self._port,
            client_id=self._client_id,
            auto_reconnect=self._want_connected,
            reconnect_attempts=self._reconnect_attempts,
            last_error=self._last_error,
        )

    async def connect(self) -> ConnectionStatus:
        if self.is_live and not self._allow_live_trading:
            self._want_connected = False
            self._last_error = (
                f"Port {self._port} is a LIVE IBKR port, but ALLOW_LIVE_TRADING is false."
            )
            raise BrokerError(self._last_error)

        self._want_connected = True
        if self._ib.isConnected():
            self._last_error = None
            self._reconnect_attempts = 0
            return self.status()

        try:
            await self._ib.connectAsync(
                host=self._host,
                port=self._port,
                clientId=self._client_id,
                timeout=8.0,
            )
            self._ib.reqMarketDataType(3)
            self._last_error = None
            self._reconnect_attempts = 0
            self._next_reconnect_at = 0.0
            self._subscribed_tickers.clear()
            self._qualified_contracts.clear()
            self._bar_cache.clear()
        except Exception as exc:
            self._reconnect_attempts += 1
            backoff = min(30.0, 2.0 * (2 ** min(self._reconnect_attempts - 1, 4)))
            self._next_reconnect_at = time.monotonic() + backoff
            self._last_error = f"Failed to connect to IBKR ({self._host}:{self._port}): {exc}"
            raise BrokerError(self._last_error) from exc

        return self.status()

    async def try_reconnect(self) -> bool:
        """Attempt reconnect with exponential backoff if connection dropped unexpectedly."""
        if not self.should_reconnect:
            return False
        try:
            await self.connect()
            return True
        except BrokerError:
            return False

    async def disconnect(self) -> ConnectionStatus:
        self._want_connected = False
        self._reconnect_attempts = 0
        if self._ib.isConnected():
            self._ib.disconnect()
        self._subscribed_tickers.clear()
        return self.status()

    def _ensure_connected(self) -> None:
        if not self._ib.isConnected():
            raise BrokerError("Not connected to IBKR TWS / IB Gateway.")

    async def _resolve_stock(self, symbol: str) -> Stock:
        # Try reqMatchingSymbolsAsync first because reqContractDetailsAsync can stall
        # indefinitely in TWS when secdef farms are slow or unresponsive.
        try:
            matches = await asyncio.wait_for(
                self._ib.reqMatchingSymbolsAsync(symbol),
                timeout=4.0,
            )
            usd_stocks = [
                m.contract
                for m in (matches or [])
                if m.contract.secType == "STK"
                and (m.contract.symbol or "").upper() == symbol
                and m.contract.currency == "USD"
                and m.contract.conId
            ]
            if usd_stocks:
                preferred_exchanges = ("NASDAQ", "NYSE", "ARCA", "BATS", "AMEX", "IEX")
                chosen = next(
                    (c for c in usd_stocks if c.primaryExchange in preferred_exchanges),
                    usd_stocks[0],
                )
                stock = Stock(
                    symbol,
                    "SMART",
                    "USD",
                    conId=int(chosen.conId),
                    primaryExchange=chosen.primaryExchange or "",
                )
                self._qualified_contracts[symbol] = stock
                return stock
        except Exception:
            pass

        contract = Stock(symbol, "SMART", "USD")
        try:
            qualified = await asyncio.wait_for(
                self._ib.qualifyContractsAsync(contract),
                timeout=4.0,
            )
        except TimeoutError as exc:
            raise BrokerError(f"Timed out qualifying contract for symbol '{symbol}'.") from exc
        if not qualified:
            raise BrokerError(f"Could not qualify contract for symbol '{symbol}'.")
        self._qualified_contracts[symbol] = qualified[0]
        return qualified[0]

    async def _qualify_stock(self, symbol: str) -> Stock:
        sym = symbol.strip().upper()
        cached = self._qualified_contracts.get(sym)
        if cached is not None:
            return cached
        task = self._qualifying_tasks.get(sym)
        if task is None or task.done():
            task = asyncio.create_task(self._resolve_stock(sym))
            self._qualifying_tasks[sym] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done() and self._qualifying_tasks.get(sym) is task:
                self._qualifying_tasks.pop(sym, None)

    async def get_account_summary(self) -> AccountSummary:
        self._ensure_connected()

        # 1. Prefer self._ib.accountValues() which is continuously streamed into memory by TWS
        raw_values = self._ib.accountValues()
        if not raw_values:
            try:
                raw_values = await asyncio.wait_for(
                    self._ib.accountSummaryAsync(), timeout=3.0
                )
            except (TimeoutError, Exception):
                raw_values = []

        by_tag: dict[str, float] = {}
        managed = self._ib.managedAccounts()
        account_id = managed[0] if managed else "IBKR"
        currency = "USD"
        for item in raw_values:
            if getattr(item, "account", None):
                account_id = item.account
            tag = item.tag
            if tag in (
                "NetLiquidation",
                "TotalCashValue",
                "BuyingPower",
                "UnrealizedPnL",
                "RealizedPnL",
            ):
                by_tag[tag] = _safe_float(item.value)
                if getattr(item, "currency", None) and item.currency != "BASE":
                    currency = item.currency
            elif "$LEDGER-UnrealizedPnL" in tag and "UnrealizedPnL" not in by_tag:
                by_tag["UnrealizedPnL"] = _safe_float(item.value)
            elif "$LEDGER-RealizedPnL" in tag and "RealizedPnL" not in by_tag:
                by_tag["RealizedPnL"] = _safe_float(item.value)

        u_pnl = by_tag.get("UnrealizedPnL", 0.0)
        r_pnl = by_tag.get("RealizedPnL", 0.0)
        return AccountSummary(
            account_id=account_id,
            net_liquidation=by_tag.get("NetLiquidation", 0.0),
            cash=by_tag.get("TotalCashValue", 0.0),
            buying_power=by_tag.get("BuyingPower", 0.0),
            unrealized_pnl=u_pnl,
            realized_pnl=r_pnl,
            daily_pnl=round(u_pnl + r_pnl, 2),
            currency=currency,
        )

    async def get_positions(self) -> list[Position]:
        self._ensure_connected()
        portfolio = self._ib.portfolio()
        if not portfolio:
            raw_positions = self._ib.positions()
            return [
                Position(
                    symbol=p.contract.symbol,
                    quantity=float(p.position),
                    avg_cost=round(float(p.avgCost), 2),
                    market_price=round(float(p.avgCost), 2),
                    market_value=round(float(p.position) * float(p.avgCost), 2),
                    unrealized_pnl=0.0,
                )
                for p in raw_positions
                if p.position != 0
            ]

        return [
            Position(
                symbol=item.contract.symbol,
                quantity=float(item.position),
                avg_cost=round(float(item.averageCost), 2),
                market_price=round(float(item.marketPrice), 2),
                market_value=round(float(item.marketValue), 2),
                unrealized_pnl=round(float(item.unrealizedPNL), 2),
            )
            for item in portfolio
            if item.position != 0
        ]

    def _trade_to_record(self, trade: Trade) -> OrderRecord:
        o = trade.order
        s = trade.orderStatus
        raw_type = o.orderType if o.orderType in ("MKT", "LMT", "STP") else "MKT"
        order_type: OrderType = raw_type  # type: ignore[assignment]
        tif: TimeInForce = "GTC" if o.tif == "GTC" else "DAY"
        return OrderRecord(
            order_id=int(o.orderId),
            symbol=trade.contract.symbol,
            side="BUY" if o.action == "BUY" else "SELL",
            quantity=int(o.totalQuantity),
            filled_quantity=int(s.filled or 0),
            order_type=order_type,
            limit_price=float(o.lmtPrice) if o.lmtPrice else None,
            stop_price=float(o.auxPrice) if o.auxPrice else None,
            parent_order_id=int(o.parentId) if getattr(o, "parentId", 0) else None,
            avg_fill_price=float(s.avgFillPrice) if s.avgFillPrice else None,
            tif=tif,
            status=_map_status(s.status),
            source="ibkr",
        )

    async def get_orders(self) -> list[OrderRecord]:
        self._ensure_connected()
        trades = self._ib.trades()
        return [self._trade_to_record(t) for t in reversed(trades)]

    async def place_order(self, order: OrderRequest) -> OrderRecord:
        self._ensure_connected()
        contract = await self._qualify_stock(order.symbol)
        base_id = self._ib.client.getReqId()
        ib_orders = build_ib_orders(order, base_id)
        for extra_idx in range(1, len(ib_orders)):
            ib_orders[extra_idx].orderId = self._ib.client.getReqId()

        trades = [self._ib.placeOrder(contract, ib_ord) for ib_ord in ib_orders]
        record = self._trade_to_record(trades[0])
        record.stop_loss_price = order.stop_loss_price
        record.take_profit_price = order.take_profit_price
        record.source = order.source
        return record

    async def cancel_order(self, order_id: int) -> OrderRecord:
        self._ensure_connected()
        target = next(
            (t for t in self._ib.openTrades() if int(t.order.orderId) == order_id),
            None,
        )
        if target is None:
            raise BrokerError(f"Open IBKR order {order_id} not found.")
        self._ib.cancelOrder(target.order)
        return self._trade_to_record(target)

    async def cancel_all_orders(self) -> int:
        self._ensure_connected()
        open_trades = self._ib.openTrades()
        self._ib.reqGlobalCancel()
        return len(open_trades)

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        self._ensure_connected()
        quotes: list[Quote] = []
        for raw in symbols:
            sym = raw.strip().upper()
            if not sym:
                continue
            ticker = self._subscribed_tickers.get(sym)
            if ticker is None:
                try:
                    contract = await self._qualify_stock(sym)
                    ticker = self._ib.reqMktData(contract, "", False, False)
                    self._subscribed_tickers[sym] = ticker
                except Exception:
                    continue

            last = (
                _safe_float(ticker.last)
                or _safe_float(ticker.marketPrice())
                or _safe_float(ticker.close)
            )
            bid = _safe_float(ticker.bid, last)
            ask = _safe_float(ticker.ask, last)
            close_p = _safe_float(ticker.close, last)
            change_pct = ((last - close_p) / close_p * 100.0) if close_p else 0.0
            quotes.append(
                Quote(
                    symbol=sym,
                    bid=round(bid, 2),
                    ask=round(ask, 2),
                    last=round(last, 2),
                    change_pct=round(change_pct, 2),
                    volume=int(_safe_float(ticker.volume, 0)),
                )
            )
        return quotes

    @staticmethod
    def _format_bar_time(bar_date: Any, include_date: bool = False) -> str:
        if hasattr(bar_date, "strftime"):
            return bar_date.strftime("%Y-%m-%d %H:%M" if include_date else "%H:%M")
        return str(bar_date)

    async def fetch_historical_bars(
        self,
        symbol: str,
        duration_str: str = "1 D",
        bar_size: str = "5 mins",
        use_rth: bool = True,
    ) -> list[PriceBar]:
        self._ensure_connected()
        contract = await self._qualify_stock(symbol.strip().upper())
        try:
            bars = await asyncio.wait_for(
                self._ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime="",
                    durationStr=duration_str,
                    barSizeSetting=bar_size,
                    whatToShow="TRADES",
                    useRTH=use_rth,
                ),
                timeout=10.0,
            )
        except TimeoutError as exc:
            raise BrokerError(f"Timed out fetching historical bars for '{symbol}'.") from exc
        include_date = bar_size in ("1 day", "1 hour", "5 mins", "15 mins")
        return [
            PriceBar(
                time=self._format_bar_time(b.date, include_date=include_date),
                open=round(float(b.open), 2),
                high=round(float(b.high), 2),
                low=round(float(b.low), 2),
                close=round(float(b.close), 2),
                volume=int(b.volume),
            )
            for b in (bars or [])
        ]

    @staticmethod
    def _duration_for_timeframe(timeframe: Timeframe, count: int) -> str:
        if timeframe == "1 day":
            if count > 220:
                years = max(1, math.ceil((count * 1.55) / 252))
                return f"{years} Y"
            return f"{max(30, math.ceil(count * 1.6))} D"
        if timeframe == "1 hour":
            return f"{max(5, math.ceil(count / 6))} D"
        if timeframe == "15 mins":
            return f"{max(2, math.ceil(count / 24))} D"
        return f"{max(1, math.ceil(count / 78))} D"

    async def get_historical_bars(
        self, symbol: str, count: int = 60, timeframe: Timeframe = "5 mins"
    ) -> list[PriceBar]:
        self._ensure_connected()
        sym = symbol.strip().upper()
        cache_ttl = 60.0 if timeframe == "1 day" else (20.0 if timeframe == "1 hour" else 3.0)
        cache_key = (sym, timeframe, count)
        cached_entry = self._bar_cache.get(cache_key)
        if cached_entry is not None and (time.monotonic() - cached_entry[0]) < cache_ttl:
            return list(cached_entry[1])

        contract = await self._qualify_stock(sym)
        duration_str = self._duration_for_timeframe(timeframe, count)
        try:
            bars = await asyncio.wait_for(
                self._ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime="",
                    durationStr=duration_str,
                    barSizeSetting=timeframe,
                    whatToShow="TRADES",
                    useRTH=(timeframe == "1 day"),
                ),
                timeout=10.0,
            )
        except TimeoutError as exc:
            raise BrokerError(f"Timed out fetching historical bars for '{symbol}'.") from exc
        sliced = bars[-count:] if bars else []
        include_date = timeframe in ("1 day", "1 hour")
        result = [
            PriceBar(
                time=self._format_bar_time(b.date, include_date=include_date),
                open=round(float(b.open), 2),
                high=round(float(b.high), 2),
                low=round(float(b.low), 2),
                close=round(float(b.close), 2),
                volume=int(b.volume),
            )
            for b in sliced
        ]
        self._bar_cache[cache_key] = (time.monotonic(), result)
        return list(result)
