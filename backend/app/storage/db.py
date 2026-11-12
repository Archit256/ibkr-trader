"""SQLite persistence for orders, strategy configs, equity snapshots, and activity logs."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models import ActivityEvent, BarCacheSummary, OrderRecord, PriceBar, utc_now


class TradingStore:
    """Thread-safe SQLite store for durable trading state across restarts."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    order_id INTEGER PRIMARY KEY,
                    broker_mode TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    filled_quantity INTEGER NOT NULL,
                    order_type TEXT NOT NULL,
                    limit_price REAL,
                    stop_price REAL,
                    stop_loss_price REAL,
                    take_profit_price REAL,
                    parent_order_id INTEGER,
                    avg_fill_price REAL,
                    tif TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS strategy_configs (
                    name TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    running INTEGER NOT NULL,
                    params_json TEXT NOT NULL,
                    last_signal TEXT,
                    last_checked_at TEXT,
                    trades_placed INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS equity_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    broker_mode TEXT NOT NULL,
                    net_liquidation REAL NOT NULL,
                    cash REAL NOT NULL,
                    daily_pnl REAL NOT NULL,
                    timestamp TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS activity_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level TEXT NOT NULL,
                    category TEXT NOT NULL,
                    message TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS historical_bars (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    bar_time TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    PRIMARY KEY (symbol, timeframe, bar_time)
                );

                CREATE TABLE IF NOT EXISTS bots (
                    bot_id TEXT PRIMARY KEY,
                    strategy_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    running INTEGER NOT NULL,
                    stop_loss_pct REAL,
                    take_profit_pct REAL,
                    params_json TEXT NOT NULL,
                    last_signal TEXT,
                    last_checked_at TEXT,
                    trades_placed INTEGER NOT NULL DEFAULT 0
                );
                """
            )

    def upsert_order(self, order: OrderRecord, broker_mode: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO orders (
                    order_id, broker_mode, symbol, side, quantity, filled_quantity,
                    order_type, limit_price, stop_price, stop_loss_price, take_profit_price,
                    parent_order_id, avg_fill_price, tif, status, source, message,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    filled_quantity=excluded.filled_quantity,
                    avg_fill_price=excluded.avg_fill_price,
                    status=excluded.status,
                    message=excluded.message,
                    updated_at=excluded.updated_at
                """,
                (
                    order.order_id,
                    broker_mode,
                    order.symbol,
                    order.side,
                    order.quantity,
                    order.filled_quantity,
                    order.order_type,
                    order.limit_price,
                    order.stop_price,
                    order.stop_loss_price,
                    order.take_profit_price,
                    order.parent_order_id,
                    order.avg_fill_price,
                    order.tif,
                    order.status,
                    order.source,
                    order.message,
                    order.created_at.isoformat(),
                    order.updated_at.isoformat(),
                ),
            )

    def list_orders(self, broker_mode: str | None = None, limit: int = 200) -> list[OrderRecord]:
        with self._connect() as conn:
            if broker_mode:
                rows = conn.execute(
                    "SELECT * FROM orders WHERE broker_mode = ? ORDER BY created_at DESC LIMIT ?",
                    (broker_mode, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            OrderRecord(
                order_id=r["order_id"],
                symbol=r["symbol"],
                side=r["side"],
                quantity=r["quantity"],
                filled_quantity=r["filled_quantity"],
                order_type=r["order_type"],
                limit_price=r["limit_price"],
                stop_price=r["stop_price"],
                stop_loss_price=r["stop_loss_price"],
                take_profit_price=r["take_profit_price"],
                parent_order_id=r["parent_order_id"],
                avg_fill_price=r["avg_fill_price"],
                tif=r["tif"],
                status=r["status"],
                source=r["source"],
                message=r["message"],
                created_at=datetime.fromisoformat(r["created_at"]),
                updated_at=datetime.fromisoformat(r["updated_at"]),
            )
            for r in rows
        ]

    def save_strategy_state(
        self,
        name: str,
        symbol: str,
        running: bool,
        params: dict[str, Any],
        last_signal: str | None,
        last_checked_at: datetime | None,
        trades_placed: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO strategy_configs (
                    name, symbol, running, params_json, last_signal, last_checked_at, trades_placed
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    symbol=excluded.symbol,
                    running=excluded.running,
                    params_json=excluded.params_json,
                    last_signal=excluded.last_signal,
                    last_checked_at=excluded.last_checked_at,
                    trades_placed=excluded.trades_placed
                """,
                (
                    name,
                    symbol,
                    1 if running else 0,
                    json.dumps(params),
                    last_signal,
                    last_checked_at.isoformat() if last_checked_at else None,
                    trades_placed,
                ),
            )

    def load_strategy_states(self) -> dict[str, dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM strategy_configs").fetchall()
        result: dict[str, dict[str, Any]] = {}
        for r in rows:
            result[r["name"]] = {
                "symbol": r["symbol"],
                "running": bool(r["running"]),
                "params": json.loads(r["params_json"]),
                "last_signal": r["last_signal"],
                "last_checked_at": (
                    datetime.fromisoformat(r["last_checked_at"])
                    if r["last_checked_at"]
                    else None
                ),
                "trades_placed": r["trades_placed"],
            }
        return result

    def append_activity(self, level: str, category: str, message: str) -> ActivityEvent:
        ts = utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO activity_events (level, category, message, timestamp) VALUES (?, ?, ?, ?)",
                (level, category, message, ts.isoformat()),
            )
            event_id = int(cur.lastrowid or 0)
        return ActivityEvent(
            id=event_id,
            level=level,  # type: ignore[arg-type]
            category=category,
            message=message,
            timestamp=ts,
        )

    def list_activity(self, limit: int = 80) -> list[ActivityEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM activity_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            ActivityEvent(
                id=r["id"],
                level=r["level"],
                category=r["category"],
                message=r["message"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
            )
            for r in rows
        ]

    def record_equity_snapshot(
        self, broker_mode: str, net_liquidation: float, cash: float, daily_pnl: float
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO equity_snapshots (broker_mode, net_liquidation, cash, daily_pnl, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (broker_mode, net_liquidation, cash, daily_pnl, utc_now().isoformat()),
            )

    def upsert_historical_bars(
        self, symbol: str, timeframe: str, bars: list[PriceBar], source: str
    ) -> int:
        if not bars:
            return 0
        sym = symbol.strip().upper()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO historical_bars (
                    symbol, timeframe, bar_time, open, high, low, close, volume, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe, bar_time) DO UPDATE SET
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    volume=excluded.volume,
                    source=excluded.source
                """,
                [
                    (sym, timeframe, b.time, b.open, b.high, b.low, b.close, b.volume, source)
                    for b in bars
                ],
            )
        return len(bars)

    def load_historical_bars(
        self, symbol: str, timeframe: str, limit: int = 20000
    ) -> list[PriceBar]:
        sym = symbol.strip().upper()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT bar_time, open, high, low, close, volume
                FROM (
                    SELECT bar_time, open, high, low, close, volume
                    FROM historical_bars
                    WHERE symbol = ? AND timeframe = ?
                    ORDER BY bar_time DESC
                    LIMIT ?
                )
                ORDER BY bar_time ASC
                """,
                (sym, timeframe, limit),
            ).fetchall()
        return [
            PriceBar(
                time=r["bar_time"],
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                volume=r["volume"],
            )
            for r in rows
        ]

    def list_bar_cache_summaries(self) -> list[BarCacheSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT symbol, timeframe, MAX(source) AS source,
                       COUNT(*) AS bar_count, MIN(bar_time) AS start_time, MAX(bar_time) AS end_time
                FROM historical_bars
                GROUP BY symbol, timeframe
                ORDER BY symbol ASC, timeframe ASC
                """
            ).fetchall()
        return [
            BarCacheSummary(
                symbol=r["symbol"],
                timeframe=r["timeframe"],
                source=r["source"],
                bar_count=r["bar_count"],
                start_time=r["start_time"],
                end_time=r["end_time"],
            )
            for r in rows
        ]

    def save_bot(
        self,
        bot_id: str,
        strategy_type: str,
        symbol: str,
        timeframe: str,
        running: bool,
        stop_loss_pct: float | None,
        take_profit_pct: float | None,
        params: dict[str, Any],
        last_signal: str | None,
        last_checked_at: datetime | None,
        trades_placed: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bots (
                    bot_id, strategy_type, symbol, timeframe, running,
                    stop_loss_pct, take_profit_pct, params_json,
                    last_signal, last_checked_at, trades_placed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_id) DO UPDATE SET
                    strategy_type=excluded.strategy_type,
                    symbol=excluded.symbol,
                    timeframe=excluded.timeframe,
                    running=excluded.running,
                    stop_loss_pct=excluded.stop_loss_pct,
                    take_profit_pct=excluded.take_profit_pct,
                    params_json=excluded.params_json,
                    last_signal=excluded.last_signal,
                    last_checked_at=excluded.last_checked_at,
                    trades_placed=excluded.trades_placed
                """,
                (
                    bot_id,
                    strategy_type,
                    symbol,
                    timeframe,
                    1 if running else 0,
                    stop_loss_pct,
                    take_profit_pct,
                    json.dumps(params),
                    last_signal,
                    last_checked_at.isoformat() if last_checked_at else None,
                    trades_placed,
                ),
            )

    def load_bots(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM bots ORDER BY bot_id ASC").fetchall()
        return [
            {
                "bot_id": r["bot_id"],
                "strategy_type": r["strategy_type"],
                "symbol": r["symbol"],
                "timeframe": r["timeframe"],
                "running": bool(r["running"]),
                "stop_loss_pct": r["stop_loss_pct"],
                "take_profit_pct": r["take_profit_pct"],
                "params": json.loads(r["params_json"]),
                "last_signal": r["last_signal"],
                "last_checked_at": (
                    datetime.fromisoformat(r["last_checked_at"])
                    if r["last_checked_at"]
                    else None
                ),
                "trades_placed": r["trades_placed"],
            }
            for r in rows
        ]

    def delete_bot(self, bot_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM bots WHERE bot_id = ?", (bot_id,))

