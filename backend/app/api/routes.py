"""FastAPI REST endpoints for connection, portfolio, orders, market data, risk, and strategies."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.broker.base import BrokerError
from app.models import (
    AccountSummary,
    BacktestRequest,
    BacktestResult,
    BotBudgetPatch,
    BotCreateRequest,
    BotDryRunPreview,
    BrokerMode,
    ConnectionStatus,
    DashboardSnapshot,
    DataSyncRequest,
    DualMomentumPreview,
    MarketStatus,
    MomentumMode,
    NewsCheckResult,
    OrderRecord,
    OrderRequest,
    PortfolioBacktestRequest,
    PortfolioBacktestResult,
    Position,
    PriceBar,
    Quote,
    ReconciliationReport,
    RiskLimitsPatch,
    RiskStatus,
    RotationBacktestRequest,
    RotationWalkForwardRequest,
    RotationWalkForwardResult,
    StrategyInfo,
    StrategyStartRequest,
    UniverseResponse,
    WalkForwardRequest,
    WalkForwardResult,
)
from app.risk.guard import RiskViolation
from app.service import TradingService

router = APIRouter(prefix="/api", tags=["trading"])


def get_service(request: Request) -> TradingService:
    return request.app.state.trading_service


class ModeSwitchRequest(BaseModel):
    mode: BrokerMode
    host: str | None = None
    port: int | None = None
    client_id: int | None = None


class KillSwitchRequest(BaseModel):
    halted: bool
    cancel_open_orders: bool = True
    reason: str | None = None


class WatchlistSymbolRequest(BaseModel):
    symbol: str


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/snapshot", response_model=DashboardSnapshot)
async def get_snapshot(svc: TradingService = Depends(get_service)) -> DashboardSnapshot:
    return await svc.snapshot()


@router.get("/connection", response_model=ConnectionStatus)
async def get_connection(svc: TradingService = Depends(get_service)) -> ConnectionStatus:
    return svc.connection_status()


@router.post("/connection/connect", response_model=ConnectionStatus)
async def connect_broker(svc: TradingService = Depends(get_service)) -> ConnectionStatus:
    try:
        return await svc.connect()
    except BrokerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/connection/disconnect", response_model=ConnectionStatus)
async def disconnect_broker(svc: TradingService = Depends(get_service)) -> ConnectionStatus:
    return await svc.disconnect()


@router.post("/connection/mode", response_model=ConnectionStatus)
async def switch_mode(
    body: ModeSwitchRequest,
    svc: TradingService = Depends(get_service),
) -> ConnectionStatus:
    return await svc.switch_mode(
        mode=body.mode,
        host=body.host,
        port=body.port,
        client_id=body.client_id,
    )


@router.post("/connection/reconcile", response_model=ReconciliationReport)
async def reconcile_state(
    svc: TradingService = Depends(get_service),
) -> ReconciliationReport:
    try:
        return await svc.reconcile_broker_state()
    except BrokerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/market/status", response_model=MarketStatus)
async def get_market_status(
    svc: TradingService = Depends(get_service),
) -> MarketStatus:
    return svc.market_status()


@router.get("/market/news/{symbol}", response_model=NewsCheckResult)
async def get_market_news(
    symbol: str,
    refresh: bool = Query(default=False),
    svc: TradingService = Depends(get_service),
) -> NewsCheckResult:
    return await svc.check_news(symbol, force_refresh=refresh)


@router.get("/account", response_model=AccountSummary)
async def get_account(svc: TradingService = Depends(get_service)) -> AccountSummary:
    try:
        return await svc.broker.get_account_summary()
    except BrokerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/positions", response_model=list[Position])
async def get_positions(svc: TradingService = Depends(get_service)) -> list[Position]:
    try:
        return await svc.broker.get_positions()
    except BrokerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/orders", response_model=list[OrderRecord])
async def get_orders(svc: TradingService = Depends(get_service)) -> list[OrderRecord]:
    try:
        return await svc.broker.get_orders()
    except BrokerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/orders", response_model=OrderRecord)
async def place_order(
    order: OrderRequest,
    svc: TradingService = Depends(get_service),
) -> OrderRecord:
    try:
        return await svc.submit_order(order)
    except RiskViolation as exc:
        svc.log_activity("error", "risk", f"Order rejected: {exc}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BrokerError as exc:
        svc.log_activity("error", "order", f"Broker rejected order: {exc}")
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/orders/{order_id}", response_model=OrderRecord)
async def cancel_order(
    order_id: int,
    svc: TradingService = Depends(get_service),
) -> OrderRecord:
    try:
        return await svc.cancel_order(order_id)
    except BrokerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/market/quotes", response_model=list[Quote])
async def get_quotes(
    symbols: str | None = Query(default=None),
    svc: TradingService = Depends(get_service),
) -> list[Quote]:
    sym_list = (
        [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if symbols
        else svc.watchlist
    )
    try:
        return await svc.broker.get_quotes(sym_list)
    except BrokerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/market/history/{symbol}", response_model=list[PriceBar])
async def get_history(
    symbol: str,
    count: int = Query(default=60, ge=10, le=200),
    svc: TradingService = Depends(get_service),
) -> list[PriceBar]:
    try:
        return await svc.get_historical_bars(symbol, count=count)
    except BrokerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/market/watchlist", response_model=list[str])
async def add_watchlist(
    body: WatchlistSymbolRequest,
    svc: TradingService = Depends(get_service),
) -> list[str]:
    return svc.add_watchlist_symbol(body.symbol)


@router.delete("/market/watchlist/{symbol}", response_model=list[str])
async def remove_watchlist(
    symbol: str,
    svc: TradingService = Depends(get_service),
) -> list[str]:
    return svc.remove_watchlist_symbol(symbol)


@router.get("/risk", response_model=RiskStatus)
async def get_risk(svc: TradingService = Depends(get_service)) -> RiskStatus:
    return svc.risk_status()


@router.post("/risk/kill-switch", response_model=RiskStatus)
async def set_kill_switch(
    body: KillSwitchRequest,
    svc: TradingService = Depends(get_service),
) -> RiskStatus:
    return await svc.set_kill_switch(
        halted=body.halted,
        cancel_open_orders=body.cancel_open_orders,
        reason=body.reason,
    )


@router.patch("/risk/limits", response_model=RiskStatus)
async def patch_risk_limits(
    body: RiskLimitsPatch,
    svc: TradingService = Depends(get_service),
) -> RiskStatus:
    return svc.update_risk_limits(body)


@router.get("/strategies", response_model=list[StrategyInfo])
async def list_strategies(svc: TradingService = Depends(get_service)) -> list[StrategyInfo]:
    return svc.list_strategies()


@router.get("/strategies/dual-momentum/preview", response_model=DualMomentumPreview)
async def preview_dual_momentum(
    top_n: int | None = Query(default=None, ge=1, le=10),
    lookback_days: int | None = Query(default=None, ge=20, le=252),
    momentum_mode: MomentumMode | None = Query(default=None),
    etfs_only: bool | None = Query(default=None),
    volatility_adjusted: bool | None = Query(default=None),
    trend_sma: int | None = Query(default=None, ge=20, le=252),
    defensive_symbol: str | None = Query(default=None),
    svc: TradingService = Depends(get_service),
) -> DualMomentumPreview:
    try:
        return await svc.preview_dual_momentum(
            top_n=top_n,
            lookback_days=lookback_days,
            momentum_mode=momentum_mode,
            etfs_only=etfs_only,
            volatility_adjusted=volatility_adjusted,
            trend_sma=trend_sma,
            defensive_symbol=defensive_symbol,
        )
    except BrokerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/strategies/{name}/start", response_model=StrategyInfo)
async def start_strategy(
    name: str,
    body: StrategyStartRequest | None = None,
    svc: TradingService = Depends(get_service),
) -> StrategyInfo:
    try:
        return svc.start_strategy(name, body or StrategyStartRequest())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/strategies/{name}/stop", response_model=StrategyInfo)
async def stop_strategy(
    name: str,
    svc: TradingService = Depends(get_service),
) -> StrategyInfo:
    try:
        return svc.stop_strategy(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/bots", response_model=list[StrategyInfo])
async def list_bots(svc: TradingService = Depends(get_service)) -> list[StrategyInfo]:
    return svc.list_strategies()


@router.post("/bots", response_model=StrategyInfo)
async def create_bot(
    body: BotCreateRequest,
    svc: TradingService = Depends(get_service),
) -> StrategyInfo:
    try:
        return svc.create_bot(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bots/{bot_id}/start", response_model=StrategyInfo)
async def start_bot(
    bot_id: str,
    body: StrategyStartRequest | None = None,
    svc: TradingService = Depends(get_service),
) -> StrategyInfo:
    try:
        return svc.start_strategy(bot_id, body or StrategyStartRequest())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/bots/{bot_id}/stop", response_model=StrategyInfo)
async def stop_bot(
    bot_id: str,
    svc: TradingService = Depends(get_service),
) -> StrategyInfo:
    try:
        return svc.stop_strategy(bot_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/bots/{bot_id}/preview", response_model=BotDryRunPreview)
async def preview_bot(
    bot_id: str,
    svc: TradingService = Depends(get_service),
) -> BotDryRunPreview:
    try:
        return await svc.preview_bot(bot_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BrokerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.patch("/bots/{bot_id}/budget", response_model=StrategyInfo)
async def update_bot_budget(
    bot_id: str,
    body: BotBudgetPatch,
    svc: TradingService = Depends(get_service),
) -> StrategyInfo:
    try:
        return svc.update_bot_budget(bot_id, body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/bots/{bot_id}")
async def delete_bot(
    bot_id: str,
    svc: TradingService = Depends(get_service),
) -> dict[str, str]:
    try:
        svc.delete_bot(bot_id)
        return {"deleted": bot_id}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/universe", response_model=UniverseResponse)
async def get_universe(svc: TradingService = Depends(get_service)) -> UniverseResponse:
    return svc.universe_status()


@router.post("/backtest/sync")
async def sync_backtest_data(
    body: DataSyncRequest,
    svc: TradingService = Depends(get_service),
) -> dict[str, dict[str, int]]:
    try:
        counts = await svc.sync_historical_data(body)
        return {"synced": counts}
    except BrokerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/backtest/run", response_model=BacktestResult)
async def run_backtest_endpoint(
    body: BacktestRequest,
    svc: TradingService = Depends(get_service),
) -> BacktestResult:
    try:
        return await svc.run_backtest(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/backtest/portfolio", response_model=PortfolioBacktestResult)
async def run_portfolio_backtest_endpoint(
    body: PortfolioBacktestRequest,
    svc: TradingService = Depends(get_service),
) -> PortfolioBacktestResult:
    try:
        return await svc.run_portfolio_backtest(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/backtest/walk-forward", response_model=WalkForwardResult)
async def run_walk_forward_endpoint(
    body: WalkForwardRequest,
    svc: TradingService = Depends(get_service),
) -> WalkForwardResult:
    try:
        return await svc.run_walk_forward(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/backtest/rotation", response_model=PortfolioBacktestResult)
async def run_rotation_backtest_endpoint(
    body: RotationBacktestRequest,
    svc: TradingService = Depends(get_service),
) -> PortfolioBacktestResult:
    try:
        return await svc.run_rotation_backtest(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/backtest/rotation/walk-forward", response_model=RotationWalkForwardResult)
async def run_rotation_walk_forward_endpoint(
    body: RotationWalkForwardRequest,
    svc: TradingService = Depends(get_service),
) -> RotationWalkForwardResult:
    try:
        return await svc.run_rotation_walk_forward(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

