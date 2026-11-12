"""In-memory paper trading simulator with bracket stop-loss/take-profit support."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from app.broker.base import BrokerClient, BrokerError
from app.models import (
    AccountSummary,
    ConnectionStatus,
    OrderRecord,
    OrderRequest,
    Position,
    PriceBar,
    Quote,
    Timeframe,
    utc_now,
)

SEED_PRICES: dict[str, float] = {
    "SPY": 592.40,
    "QQQ": 506.10,
    "IWM": 236.80,
    "TLT": 91.45,
    "GLD": 244.30,
    "AAPL": 227.50,
    "MSFT": 418.20,
    "NVDA": 141.80,
    "AMZN": 208.35,
    "META": 584.10,
    "AMD": 146.20,
}


class _SimulatedPosition:
    def __init__(self, symbol: str, quantity: float, avg_cost: float) -> None:
        self.symbol = symbol
        self.quantity = quantity
        self.avg_cost = avg_cost


class PaperSimulator(BrokerClient):
    """Simulates market quotes, order fills, OCA bracket exits, cash, and P&L."""

    def __init__(self, initial_cash: float = 100_000.0, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._connected = True
        self._initial_cash = initial_cash
        self._cash = initial_cash
        self._realized_pnl = 0.0
        self._next_order_id = 1001
        self._positions: dict[str, _SimulatedPosition] = {}
        self._orders: dict[int, OrderRecord] = {}
        self._open_prices: dict[str, float] = dict(SEED_PRICES)
        self._prices: dict[str, float] = dict(SEED_PRICES)
        self._bars: dict[str, list[PriceBar]] = {}
        for sym in SEED_PRICES:
            self._init_history(sym)

    def _init_history(self, symbol: str, num_bars: int = 300) -> None:
        base = self._prices.setdefault(symbol, round(self._rng.uniform(50.0, 350.0), 2))
        self._open_prices.setdefault(symbol, base)
        now = datetime.now(timezone.utc)
        bars: list[PriceBar] = []
        price = base * 0.96
        for i in range(num_bars):
            ts = now - timedelta(minutes=num_bars - i)
            drift = (base - price) * 0.05
            delta = self._rng.gauss(drift, base * 0.0025)
            open_p = round(price, 2)
            close_p = round(max(1.0, open_p + delta), 2)
            high_p = round(max(open_p, close_p) + abs(self._rng.gauss(0, base * 0.001)), 2)
            low_p = round(max(0.5, min(open_p, close_p) - abs(self._rng.gauss(0, base * 0.001))), 2)
            bars.append(
                PriceBar(
                    time=ts.strftime("%H:%M"),
                    open=open_p,
                    high=high_p,
                    low=low_p,
                    close=close_p,
                    volume=self._rng.randint(15_000, 120_000),
                )
            )
            price = close_p
        self._prices[symbol] = bars[-1].close
        self._bars[symbol] = bars

    def set_price(self, symbol: str, price: float) -> None:
        """Force a symbol price (useful for deterministic testing) and evaluate orders."""
        sym = symbol.strip().upper()
        if sym not in self._prices:
            self._init_history(sym)
        self._prices[sym] = round(price, 2)
        self._evaluate_open_orders()

    def step_market(self, symbols: list[str] | None = None) -> None:
        """Advance simulated prices by one tick and evaluate resting limit/stop orders."""
        if not self._connected:
            return
        targets = symbols or list(self._prices.keys())
        now_label = datetime.now(timezone.utc).strftime("%H:%M")
        for sym in targets:
            if sym not in self._prices:
                self._init_history(sym)
            cur = self._prices[sym]
            pct = self._rng.gauss(0.0, 0.0018)
            nxt = round(max(1.0, cur * (1.0 + pct)), 2)
            self._prices[sym] = nxt

            bars = self._bars[sym]
            if bars and bars[-1].time == now_label:
                last_bar = bars[-1]
                last_bar.close = nxt
                last_bar.high = max(last_bar.high, nxt)
                last_bar.low = min(last_bar.low, nxt)
                last_bar.volume += self._rng.randint(500, 5000)
            else:
                bars.append(
                    PriceBar(
                        time=now_label,
                        open=cur,
                        high=max(cur, nxt),
                        low=min(cur, nxt),
                        close=nxt,
                        volume=self._rng.randint(5_000, 40_000),
                    )
                )
                if len(bars) > 300:
                    bars.pop(0)

        self._evaluate_open_orders()

    def _evaluate_open_orders(self) -> None:
        for order in list(self._orders.values()):
            if order.status != "Submitted":
                continue
            price = self._prices.get(order.symbol)
            if price is None:
                continue
            should_fill = False
            fill_price = price
            if order.order_type == "MKT":
                should_fill = True
            elif order.order_type == "LMT" and order.limit_price is not None:
                if order.side == "BUY" and price <= order.limit_price:
                    should_fill = True
                    fill_price = min(price, order.limit_price)
                elif order.side == "SELL" and price >= order.limit_price:
                    should_fill = True
                    fill_price = max(price, order.limit_price)
            elif order.order_type == "STP" and order.stop_price is not None:
                if order.side == "BUY" and price >= order.stop_price:
                    should_fill = True
                elif order.side == "SELL" and price <= order.stop_price:
                    should_fill = True

            if should_fill:
                self._fill_order(order, fill_price)

    def _spawn_bracket_children(self, parent: OrderRecord) -> None:
        exit_side = "SELL" if parent.side == "BUY" else "BUY"
        now = utc_now()
        if parent.take_profit_price is not None:
            tp_id = self._next_order_id
            self._next_order_id += 1
            self._orders[tp_id] = OrderRecord(
                order_id=tp_id,
                symbol=parent.symbol,
                side=exit_side,
                quantity=parent.quantity,
                order_type="LMT",
                limit_price=parent.take_profit_price,
                parent_order_id=parent.order_id,
                tif="GTC",
                status="Submitted",
                source=f"{parent.source}:tp",
                created_at=now,
                updated_at=now,
            )
        if parent.stop_loss_price is not None:
            sl_id = self._next_order_id
            self._next_order_id += 1
            self._orders[sl_id] = OrderRecord(
                order_id=sl_id,
                symbol=parent.symbol,
                side=exit_side,
                quantity=parent.quantity,
                order_type="STP",
                stop_price=parent.stop_loss_price,
                parent_order_id=parent.order_id,
                tif="GTC",
                status="Submitted",
                source=f"{parent.source}:sl",
                created_at=now,
                updated_at=now,
            )

    def _cancel_oca_siblings(self, filled_order: OrderRecord) -> None:
        if filled_order.parent_order_id is None:
            return
        for other in self._orders.values():
            if (
                other.order_id != filled_order.order_id
                and other.parent_order_id == filled_order.parent_order_id
                and other.status in ("PendingSubmit", "Submitted")
            ):
                other.status = "Cancelled"
                other.message = f"OCA cancelled on fill of #{filled_order.order_id}"
                other.updated_at = utc_now()

    def _fill_order(self, order: OrderRecord, fill_price: float) -> None:
        signed_qty = order.quantity if order.side == "BUY" else -order.quantity
        trade_value = signed_qty * fill_price
        self._cash -= trade_value

        pos = self._positions.get(order.symbol)
        if pos is None:
            self._positions[order.symbol] = _SimulatedPosition(
                symbol=order.symbol, quantity=signed_qty, avg_cost=fill_price
            )
        else:
            prev_qty = pos.quantity
            new_qty = prev_qty + signed_qty
            if prev_qty != 0 and (prev_qty > 0) != (signed_qty > 0):
                closed_qty = min(abs(prev_qty), abs(signed_qty))
                direction = 1 if prev_qty > 0 else -1
                self._realized_pnl += closed_qty * (fill_price - pos.avg_cost) * direction

            if new_qty == 0:
                self._positions.pop(order.symbol, None)
            elif (prev_qty > 0) == (new_qty > 0) and abs(new_qty) > abs(prev_qty):
                total_cost = abs(prev_qty) * pos.avg_cost + abs(signed_qty) * fill_price
                pos.avg_cost = total_cost / abs(new_qty)
                pos.quantity = new_qty
            elif (prev_qty > 0) != (new_qty > 0):
                pos.quantity = new_qty
                pos.avg_cost = fill_price
            else:
                pos.quantity = new_qty

        order.filled_quantity = order.quantity
        order.avg_fill_price = round(fill_price, 2)
        order.status = "Filled"
        order.updated_at = utc_now()

        if order.parent_order_id is None and (
            order.stop_loss_price is not None or order.take_profit_price is not None
        ):
            self._spawn_bracket_children(order)
        else:
            self._cancel_oca_siblings(order)

    async def connect(self) -> ConnectionStatus:
        self._connected = True
        return self.status()

    async def disconnect(self) -> ConnectionStatus:
        self._connected = False
        return self.status()

    def status(self) -> ConnectionStatus:
        return ConnectionStatus(
            broker_mode="simulator",
            connected=self._connected,
            environment="SIMULATOR",
            host="in-memory",
            port=None,
            client_id=None,
            auto_reconnect=False,
            reconnect_attempts=0,
        )

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise BrokerError("Simulator is disconnected. Reconnect first.")

    async def get_account_summary(self) -> AccountSummary:
        self._ensure_connected()
        positions = await self.get_positions()
        market_value = sum(p.market_value for p in positions)
        unrealized = sum(p.unrealized_pnl for p in positions)
        net_liq = round(self._cash + market_value, 2)
        buying_power = round(max(0.0, self._cash * 2.0), 2)
        daily_pnl = round(net_liq - self._initial_cash, 2)
        return AccountSummary(
            account_id="SIM-PAPER-001",
            net_liquidation=net_liq,
            cash=round(self._cash, 2),
            buying_power=buying_power,
            unrealized_pnl=round(unrealized, 2),
            realized_pnl=round(self._realized_pnl, 2),
            daily_pnl=daily_pnl,
            currency="USD",
        )

    async def get_positions(self) -> list[Position]:
        self._ensure_connected()
        result: list[Position] = []
        for sym, pos in sorted(self._positions.items()):
            mkt_price = self._prices.get(sym, pos.avg_cost)
            mkt_val = pos.quantity * mkt_price
            unrealized = (mkt_price - pos.avg_cost) * pos.quantity
            result.append(
                Position(
                    symbol=sym,
                    quantity=pos.quantity,
                    avg_cost=round(pos.avg_cost, 2),
                    market_price=round(mkt_price, 2),
                    market_value=round(mkt_val, 2),
                    unrealized_pnl=round(unrealized, 2),
                )
            )
        return result

    async def get_orders(self) -> list[OrderRecord]:
        self._ensure_connected()
        return sorted(
            self._orders.values(), key=lambda o: o.created_at, reverse=True
        )

    async def place_order(self, order: OrderRequest) -> OrderRecord:
        self._ensure_connected()
        if order.symbol not in self._prices:
            self._init_history(order.symbol)

        order_id = self._next_order_id
        self._next_order_id += 1
        now = utc_now()
        record = OrderRecord(
            order_id=order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            order_type=order.order_type,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            stop_loss_price=order.stop_loss_price,
            take_profit_price=order.take_profit_price,
            tif=order.tif,
            status="Submitted",
            source=order.source,
            created_at=now,
            updated_at=now,
        )
        self._orders[order_id] = record
        self._evaluate_open_orders()
        return self._orders[order_id]

    async def cancel_order(self, order_id: int) -> OrderRecord:
        self._ensure_connected()
        record = self._orders.get(order_id)
        if record is None:
            raise BrokerError(f"Order {order_id} not found")
        if record.status in ("Filled", "Cancelled", "Rejected"):
            raise BrokerError(f"Order {order_id} is already {record.status}")
        record.status = "Cancelled"
        record.updated_at = utc_now()
        return record

    async def cancel_all_orders(self) -> int:
        self._ensure_connected()
        cancelled = 0
        for record in self._orders.values():
            if record.status in ("PendingSubmit", "Submitted"):
                record.status = "Cancelled"
                record.updated_at = utc_now()
                cancelled += 1
        return cancelled

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        self._ensure_connected()
        quotes: list[Quote] = []
        for raw in symbols:
            sym = raw.strip().upper()
            if not sym:
                continue
            if sym not in self._prices:
                self._init_history(sym)
            last = self._prices[sym]
            open_p = self._open_prices.get(sym, last)
            change_pct = ((last - open_p) / open_p * 100.0) if open_p else 0.0
            spread = max(0.01, round(last * 0.0004, 2))
            quotes.append(
                Quote(
                    symbol=sym,
                    bid=round(last - spread / 2, 2),
                    ask=round(last + spread / 2, 2),
                    last=round(last, 2),
                    change_pct=round(change_pct, 2),
                    volume=self._bars[sym][-1].volume if self._bars.get(sym) else 10000,
                )
            )
        return quotes

    async def get_historical_bars(
        self, symbol: str, count: int = 60, timeframe: Timeframe = "5 mins"
    ) -> list[PriceBar]:
        self._ensure_connected()
        if count <= 0:
            return []
        sym = symbol.strip().upper()
        if sym not in self._bars:
            self._init_history(sym)
        return list(self._bars[sym][-count:])
