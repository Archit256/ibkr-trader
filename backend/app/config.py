"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LIVE_IBKR_PORTS = {7496, 4001}


ETF_UNIVERSE = [
    "SPY",
    "QQQ",
    "IWM",
    "TLT",
    "GLD",
    "XLE",
    "XLF",
    "XLV",
    "XLI",
    "XLU",
    "XLP",
    "VNQ",
]

STARTER_UNIVERSE = [
    *ETF_UNIVERSE,
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "AMD",
]


class Settings(BaseSettings):
    """Runtime settings for the IBKR trading service."""

    model_config = SettingsConfigDict(
        env_file="../.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    broker_mode: Literal["simulator", "ibkr"] = "simulator"
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497
    ibkr_client_id: int = 1
    allow_live_trading: bool = False

    max_order_qty: int = Field(default=500, gt=0)
    max_order_notional: float = Field(default=50000.0, gt=0)
    max_position_shares: int = Field(default=1000, gt=0)
    max_daily_loss: float = Field(default=5000.0, gt=0)

    default_watchlist: str = "SPY,QQQ,IWM,TLT,GLD,AAPL,MSFT,NVDA,AMZN,META,AMD"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    db_path: str = "data/trading.db"
    enforce_market_hours: bool = True

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.8-flash"
    news_guard_use_llm: bool = True
    news_guard_llm_timeout_seconds: float = 8.0

    @property
    def watchlist_symbols(self) -> list[str]:
        return [s.strip().upper() for s in self.default_watchlist.split(",") if s.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [s.strip() for s in self.cors_origins.split(",") if s.strip()]

    @property
    def is_live_port(self) -> bool:
        return self.ibkr_port in LIVE_IBKR_PORTS


@lru_cache
def get_settings() -> Settings:
    return Settings()

