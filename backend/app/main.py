"""FastAPI entrypoint with background market/strategy ticker and WebSocket stream."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings
from app.service import TradingService


class ConnectionHub:
    """Tracks active WebSocket dashboard subscribers."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self.closing = asyncio.Event()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def close_all(self) -> None:
        self.closing.set()
        for ws in list(self._clients):
            try:
                await ws.close(code=1012)
            except Exception:
                pass
            self._clients.discard(ws)

    async def broadcast_json(self, payload: str) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)


async def _engine_loop(service: TradingService, hub: ConnectionHub) -> None:
    while True:
        try:
            await service.step_engine()
            snapshot = await service.snapshot()
            await hub.broadcast_json(snapshot.model_dump_json())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            service.log_activity("error", "engine", f"Engine loop error: {exc}")
        await asyncio.sleep(1.5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    service = TradingService(settings)
    hub = ConnectionHub()
    app.state.trading_service = service
    app.state.ws_hub = hub

    task = asyncio.create_task(_engine_loop(service, hub))
    try:
        yield
    finally:
        await hub.close_all()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if service.connection_status().connected:
            await service.disconnect()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="IBKR Trader API",
        version="0.1.0",
        description="Interactive Brokers trading engine, risk guard, and live dashboard API",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.websocket("/ws")
    async def websocket_stream(ws: WebSocket) -> None:
        hub: ConnectionHub = app.state.ws_hub
        service: TradingService = app.state.trading_service
        await hub.connect(ws)
        try:
            initial = await service.snapshot()
            await ws.send_text(initial.model_dump_json())
            while not hub.closing.is_set():
                # Keep socket open; client messages can request immediate refresh
                recv_task = asyncio.create_task(ws.receive_text())
                close_task = asyncio.create_task(hub.closing.wait())
                done, pending = await asyncio.wait(
                    {recv_task, close_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for p in pending:
                    p.cancel()
                if close_task in done:
                    break
                recv_task.result()
                snap = await service.snapshot()
                await ws.send_text(snap.model_dump_json())
        except WebSocketDisconnect:
            hub.disconnect(ws)
        except Exception:
            hub.disconnect(ws)

    return app


app = create_app()

