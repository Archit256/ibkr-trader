# IBKR Trader

Full-stack Interactive Brokers trading application featuring a **FastAPI + `ib_async`** execution/risk engine and a real-time **React + TypeScript** trading dashboard.

Includes a built-in **Paper Simulator** so you can test order routing, risk guardrails, automated strategies, and the live WebSocket dashboard immediately—even before connecting to TWS or IB Gateway.

## Features

- **Dual Broker Engine (`backend/app/broker/`)**
  - **`IBKRClient`**: Async adapter using [`ib_async`](https://github.com/ib-api-reloaded/ib_async) for TWS and IB Gateway (`MKT`, `LMT`, `STP` orders, live quotes, historical bars, account summary, positions).
  - **`PaperSimulator`**: In-memory market & order fill simulator with realistic price ticks, limit/stop fill matching, and realized/unrealized P&L tracking.
- **Pre-Trade Risk Guardrails (`backend/app/risk/guard.py`)**
  - **Live Port Lock**: Blocks IBKR live ports (`7496`, `4001`) unless `ALLOW_LIVE_TRADING=true` is explicitly set.
  - **Order & Position Limits**: `MAX_ORDER_QTY`, `MAX_ORDER_NOTIONAL`, `MAX_POSITION_SHARES`.
  - **Daily Loss Circuit Breaker**: Automatically engages the kill switch if `daily_pnl <= -MAX_DAILY_LOSS`.
  - **Kill Switch**: Halts all manual and strategy orders and cancels open orders.
- **Automated Strategy Engine (`backend/app/strategies/`)**
  - Pluggable strategy runner with a starter **`SmaCrossoverStrategy`** (fast/slow SMA crossover).
  - Strategy orders flow through the same `RiskGuard` validation as manual orders.
- **Real-Time Web Dashboard (`frontend/`)**
  - Live WebSocket streaming (`/ws`), interactive OHLC + SMA candlestick chart, watchlist, order ticket, positions & orders manager, strategy controls, risk guard editor, and activity log.

## Quick Start

### 1. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

Run backend tests:
```bash
cd backend
.venv/bin/pytest -v
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173** in your browser.

### Or run both with `make`

```bash
make dev
```

## Connecting to Interactive Brokers (TWS / IB Gateway)

1. Copy `.env.example` to `.env` if you want persistent settings:
   ```bash
   cp .env.example .env
   ```
2. Open **Trader Workstation (TWS)** or **IB Gateway** in **Paper Trading** mode.
3. Enable API access in TWS/Gateway settings:
   - **Enable ActiveX and Socket Clients**
   - Default Paper ports: `7497` (TWS) or `4002` (IB Gateway)
4. In the dashboard top bar, click **Simulator Mode** → enter host/port/client ID → **Connect IBKR**.
   - Live ports (`7496` / `4001`) remain blocked unless `ALLOW_LIVE_TRADING=true` is explicitly configured in `.env`.

