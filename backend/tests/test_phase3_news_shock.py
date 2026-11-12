"""Tests for NewsGuard, ShockOverreactionStrategy, cooldowns, and EOD auto-flatten."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.backtest.engine import run_backtest
from app.broker.paper_simulator import PaperSimulator
from app.market.news_guard import (
    NewsGuard,
    NewsGuardLLMDecision,
    classify_headlines,
    is_near_earnings,
)
from app.models import BacktestRequest, NewsCheckResult, NewsHeadline, PriceBar
from app.strategies.shock_reversion import ShockOverreactionStrategy, shock_reversion_signal

ET = ZoneInfo("America/New_York")


def _bar(
    time: str,
    open_p: float,
    close_p: float,
    low_p: float | None = None,
    high_p: float | None = None,
    volume: int = 20_000,
) -> PriceBar:
    return PriceBar(
        time=time,
        open=open_p,
        high=high_p if high_p is not None else max(open_p, close_p) + 0.2,
        low=low_p if low_p is not None else min(open_p, close_p) - 0.2,
        close=close_p,
        volume=volume,
    )


def test_news_guard_detects_fundamental_catalysts_and_earnings() -> None:
    now = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)
    recent_ts = int((now - timedelta(hours=2)).timestamp())
    old_ts = int((now - timedelta(hours=30)).timestamp())

    headlines, reason = classify_headlines(
        [
            {"title": "Chip stocks see broad sector rotation", "providerPublishTime": recent_ts},
            {"title": "Company cuts Q4 guidance after revenue miss", "providerPublishTime": recent_ts},
            {"title": "Old downgrade from last week", "providerPublishTime": old_ts},
        ],
        now=now,
    )
    assert len(headlines) == 3
    assert reason is not None
    assert "guidance" in reason.lower()

    # Old catalyst alone should not block
    _, old_reason = classify_headlines(
        [{"title": "Old downgrade from last week", "providerPublishTime": old_ts}],
        now=now,
    )
    assert old_reason is None

    assert is_near_earnings([date(2026, 11, 13)], today=date(2026, 11, 12)) == "2026-11-13"
    assert is_near_earnings([date(2026, 11, 20)], today=date(2026, 11, 12)) is None


def test_shock_reversion_waits_for_green_stabilization_and_backtests() -> None:
    bars = [
        _bar(f"2026-11-11 10:{i:02d}", 100.0, 100.1, low_p=99.6, high_p=100.5, volume=20_000)
        for i in range(22)
    ]
    # Panic shock bar: drops ~3.5x ATR on 4x volume
    bars.append(_bar("2026-11-11 10:22", 100.0, 96.5, low_p=96.2, high_p=100.1, volume=90_000))

    # Red continuation bar -> should NOT enter yet
    bars_cont = [*bars, _bar("2026-11-11 10:23", 96.5, 96.0, low_p=95.8, high_p=96.6, volume=50_000)]
    act_wait, _, _, _ = shock_reversion_signal(bars_cont, has_position=False, params={})
    assert act_wait == "HOLD"

    # Green stabilization bar holding above shock low -> BUY with SL below shock low
    bars_rev = [*bars, _bar("2026-11-11 10:23", 96.4, 97.2, low_p=96.3, high_p=97.4, volume=45_000)]
    act_buy, _, sl, tp = shock_reversion_signal(bars_rev, has_position=False, params={})
    assert act_buy == "BUY"
    assert sl is not None and sl < 96.2
    assert tp is not None and tp > 97.2

    # Backtest full sequence through bounce exit
    bars_rev.append(_bar("2026-11-11 10:24", 97.3, 100.2, low_p=97.2, high_p=100.4, volume=30_000))
    res = run_backtest(
        bars_rev,
        BacktestRequest(
            strategy_type="shock_reversion",
            symbol="NVDA",
            timeframe="5 mins",
            initial_capital=10_000.0,
            quantity=10,
            stop_loss_pct=2.0,
        ),
    )
    assert res.total_trades == 1
    assert res.trades[0].pnl > 0


async def test_shock_strategy_news_veto_cooldown_and_auto_flatten() -> None:
    sim = PaperSimulator(initial_cash=100_000.0, seed=1)
    strat = ShockOverreactionStrategy(bot_id="nvda-shock", symbol="NVDA", timeframe="5 mins")

    # Auto-flatten check: 15:57 ET triggers for 5 mins bot, not at 11:00 ET
    assert strat.should_auto_flatten(datetime(2026, 11, 11, 15, 57, tzinfo=ET)) is True
    assert strat.should_auto_flatten(datetime(2026, 11, 11, 11, 0, tzinfo=ET)) is False

    # Construct shock + stabilization bars directly on simulator
    bars = [
        _bar(f"2026-11-11 10:{i:02d}", 100.0, 100.1, low_p=99.6, high_p=100.5, volume=20_000)
        for i in range(22)
    ]
    bars.append(_bar("2026-11-11 10:22", 100.0, 96.5, low_p=96.2, high_p=100.1, volume=90_000))
    bars.append(_bar("2026-11-11 10:23", 96.4, 97.2, low_p=96.3, high_p=97.4, volume=45_000))
    sim._bars["NVDA"] = bars  # noqa: SLF001
    sim._prices["NVDA"] = 97.2  # noqa: SLF001

    placed = []

    async def fake_submit(order):
        placed.append(order)
        return await sim.place_order(order)

    logs = []

    def fake_log(lvl, cat, msg):
        logs.append((lvl, cat, msg))

    async def blocked_news(sym: str) -> NewsCheckResult:
        return NewsCheckResult(symbol=sym, blocked=True, reason="Earnings blackout window")

    msg = await strat.evaluate(sim, fake_submit, fake_log, news_checker=blocked_news)
    assert "NewsGuard vetoed" in msg
    assert len(placed) == 0

    async def clear_news(sym: str) -> NewsCheckResult:
        return NewsCheckResult(symbol=sym, blocked=False, reason="Clear")

    msg2 = await strat.evaluate(sim, fake_submit, fake_log, news_checker=clear_news)
    assert msg2.startswith("BUY 10 NVDA")
    assert len(placed) == 1
    assert placed[0].stop_loss_price is not None and placed[0].stop_loss_price < 96.2
    assert strat.is_in_cooldown() is True


async def test_news_guard_llm_rationale_overrides_keywords_and_falls_back() -> None:
    bullish_headlines = [
        NewsHeadline(
            title="NVDA beats Q4 earnings and raises full-year guidance",
            publisher="Reuters",
            published_at="2026-11-12 14:00 UTC",
            catalyst_match="earnings",
        ),
        NewsHeadline(
            title="Chip sector rises on macro data",
            publisher="Bloomberg",
            published_at="2026-11-12 13:30 UTC",
        ),
    ]

    async def bullish_llm(sym, headlines, earnings_date):
        return NewsGuardLLMDecision(
            blocked=False,
            classification="BULLISH_CATALYST",
            confidence=0.92,
            rationale="Headline reports an earnings beat and raised guidance rather than negative deterioration.",
            flagged_headline_indices=[],
        )

    guard_clear = NewsGuard(
        model="gemini-3.8-flash",
        llm_evaluator=bullish_llm,
        data_fetcher=lambda sym: (bullish_headlines, "Recent earnings headline", [], "2026-12-20"),
    )
    res_clear = await guard_clear.check("NVDA")
    assert res_clear.blocked is False
    assert res_clear.classification == "BULLISH_CATALYST"
    assert res_clear.analysis_source == "gemini"
    assert res_clear.confidence == 0.92
    assert res_clear.rationale is not None and "earnings beat" in res_clear.rationale

    # Structural risk without hardcoded keywords should still be blocked by LLM
    stealth_risk_headlines = [
        NewsHeadline(
            title="Major hyperscaler halts GPU orders after thermal design defect",
            publisher="WSJ",
            published_at="2026-11-12 14:15 UTC",
        )
    ]

    async def risk_llm(sym, headlines, earnings_date):
        return NewsGuardLLMDecision(
            blocked=True,
            classification="FUNDAMENTAL_RISK",
            confidence=0.88,
            rationale="Order halt due to hardware defect represents a company-specific demand risk.",
            flagged_headline_indices=[0],
        )

    guard_risk = NewsGuard(
        model="gemini-3.8-flash",
        llm_evaluator=risk_llm,
        data_fetcher=lambda sym: (stealth_risk_headlines, None, [], None),
    )
    res_risk = await guard_risk.check("NVDA")
    assert res_risk.blocked is True
    assert res_risk.classification == "FUNDAMENTAL_RISK"
    assert res_risk.analysis_source == "gemini"
    assert res_risk.headlines[0].catalyst_match == "LLM: fundamental risk"

    # Hard earnings blackout must still block even if LLM evaluator exists
    guard_earn = NewsGuard(
        llm_evaluator=bullish_llm,
        data_fetcher=lambda sym: (bullish_headlines, None, [date.today()], date.today().isoformat()),
    )
    res_earn = await guard_earn.check("NVDA")
    assert res_earn.blocked is True
    assert res_earn.classification == "EARNINGS_BLACKOUT"

    # LLM failure gracefully falls back to keyword heuristic
    async def failing_llm(sym, headlines, earnings_date):
        raise RuntimeError("API timeout")

    guard_fallback = NewsGuard(
        llm_evaluator=failing_llm,
        data_fetcher=lambda sym: (bullish_headlines, "Recent earnings headline", [], None),
    )
    res_fb = await guard_fallback.check("NVDA")
    assert res_fb.blocked is True
    assert res_fb.analysis_source == "heuristic"
    assert res_fb.rationale is not None and "keyword heuristic" in res_fb.rationale.lower()

