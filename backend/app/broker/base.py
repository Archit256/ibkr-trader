"""Abstract broker interface implemented by IBKRClient and PaperSimulator."""

from abc import ABC, abstractmethod

from app.models import (
    AccountSummary,
    ConnectionStatus,
    OrderRecord,
    OrderRequest,
    Position,
    PriceBar,
    Quote,
    Timeframe,
)


class BrokerError(Exception):
    """Raised when a broker operation fails or is rejected."""


class BrokerClient(ABC):
    """Unified async interface for interacting with IBKR or the paper simulator."""

    @abstractmethod
    async def connect(self) -> ConnectionStatus:
        """Establish a connection to the broker."""

    @abstractmethod
    async def disconnect(self) -> ConnectionStatus:
        """Close the broker connection cleanly."""

    @abstractmethod
    def status(self) -> ConnectionStatus:
        """Return current connection status."""

    @abstractmethod
    async def get_account_summary(self) -> AccountSummary:
        """Return account equity, cash, buying power, and P&L."""

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Return currently held positions."""

    @abstractmethod
    async def get_orders(self) -> list[OrderRecord]:
        """Return open and recent orders."""

    @abstractmethod
    async def place_order(self, order: OrderRequest) -> OrderRecord:
        """Submit a new order to the broker."""

    @abstractmethod
    async def cancel_order(self, order_id: int) -> OrderRecord:
        """Cancel an open order by ID."""

    @abstractmethod
    async def cancel_all_orders(self) -> int:
        """Cancel all open orders and return count cancelled."""

    @abstractmethod
    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        """Fetch latest quotes for a list of ticker symbols."""

    @abstractmethod
    async def get_historical_bars(
        self, symbol: str, count: int = 60, timeframe: Timeframe = "5 mins"
    ) -> list[PriceBar]:
        """Fetch recent OHLCV bars for charting and strategy evaluation."""

