"""News and earnings blackout guard with Gemini LLM rationale and heuristic fallback."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

from app.models import NewsCheckResult, NewsHeadline

# Fundamental repricing keywords used as fallback when LLM rationale is unavailable
FUNDAMENTAL_CATALYST_KEYWORDS = (
    "earnings",
    "guidance",
    "miss",
    "downgrade",
    "sec ",
    "investigation",
    "lawsuit",
    "bankruptcy",
    "chapter 11",
    "offering",
    "dilution",
    "recall",
    "fraud",
    "layoff",
    "fda",
    "subpoena",
    "delist",
)

NEWS_GUARD_SYSTEM_INSTRUCTION = """You are NewsGuard, a pre-trade risk analyst for automated US equity mean-reversion and dip-buying bots.
Your job is to decide whether recent news around a ticker reflects a REAL fundamental repricing catalyst (making dip-buying unsafe like catching a falling knife) or benign/non-fundamental noise (safe for technical mean reversion).

Block (`blocked=true`, `classification="FUNDAMENTAL_RISK"`) ONLY when recent news (typically within the past 24 hours) indicates structural, company-specific downside or binary uncertainty, such as:
- Earnings miss, guidance cut, margin warning, or pre-announcement
- Regulatory/SEC/DOJ probe, subpoena, lawsuit, fraud, accounting review, or product recall
- Capital raise, secondary offering, debt restructuring, bankruptcy, or dilution
- Executive abrupt departure, major customer loss, export ban, or thesis-breaking analyst downgrade

Do NOT block (`blocked=false`) for:
- Routine market commentary, broad sector/macro chatter, ETF flows, or analyst price-target bumps
- Positive earnings beats / guidance raises (`classification="BULLISH_CATALYST"`)
- Unrelated macro/market noise (`classification="MACRO_NOISE"`) or stale news older than ~24 hours (`classification="CLEAR"`)

Respond strictly in the requested JSON schema. Be concise and cite the specific headline(s) driving your rationale."""


class NewsGuardLLMDecision(BaseModel):
    """Structured Gemini rationale output for NewsGuard evaluation."""

    blocked: bool = Field(
        description="True if dip-buying should be vetoed due to a real fundamental downside/binary catalyst."
    )
    classification: Literal[
        "CLEAR",
        "MACRO_NOISE",
        "BULLISH_CATALYST",
        "FUNDAMENTAL_RISK",
    ] = Field(description="High-level category of the news backdrop.")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in the classification from 0.0 to 1.0.",
    )
    rationale: str = Field(
        description="1-3 sentence explanation citing relevant headlines and why dip-buying is safe or unsafe."
    )
    flagged_headline_indices: list[int] = Field(
        default_factory=list,
        description="0-based indices of headlines that represent fundamental risk (empty if blocked=false).",
    )


LLMEvaluator = Callable[
    [str, list[NewsHeadline], str | None],
    Awaitable[NewsGuardLLMDecision],
]


def classify_headlines(
    raw_items: list[dict[str, Any]],
    now: datetime | None = None,
    lookback_hours: float = 12.0,
) -> tuple[list[NewsHeadline], str | None]:
    """Parse raw news entries and return (headlines, matched_fundamental_catalyst)."""
    ref_now = now or datetime.now(timezone.utc)
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=timezone.utc)
    parsed: list[NewsHeadline] = []
    matched_reason: str | None = None

    for item in raw_items[:8]:
        content = item.get("content") if isinstance(item.get("content"), dict) else item
        title = str(content.get("title") or item.get("title") or "").strip()
        if not title:
            continue

        summary = str(content.get("summary") or item.get("summary") or "").strip()
        pub_obj = content.get("provider") or item.get("publisher") or ""
        publisher = (
            str(pub_obj.get("displayName", ""))
            if isinstance(pub_obj, dict)
            else str(pub_obj)
        )
        link_obj = content.get("canonicalUrl") or item.get("link") or ""
        link = (
            str(link_obj.get("url", ""))
            if isinstance(link_obj, dict)
            else str(link_obj)
        )

        pub_dt: datetime | None = None
        pub_str = ""
        raw_ts = content.get("pubDate") or item.get("providerPublishTime")
        if isinstance(raw_ts, (int, float)):
            pub_dt = datetime.fromtimestamp(raw_ts, tz=timezone.utc)
            pub_str = pub_dt.strftime("%Y-%m-%d %H:%M UTC")
        elif isinstance(raw_ts, str) and raw_ts:
            pub_str = raw_ts[:19].replace("T", " ")
            try:
                pub_dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                pub_dt = None

        is_recent = True
        if pub_dt is not None:
            age_hours = (ref_now - pub_dt).total_seconds() / 3600.0
            is_recent = age_hours <= lookback_hours

        lowered = f" {title.lower()} "
        catalyst = next(
            (kw.strip() for kw in FUNDAMENTAL_CATALYST_KEYWORDS if kw in lowered),
            None,
        )
        if catalyst and is_recent and matched_reason is None:
            matched_reason = f"Recent {catalyst} headline: '{title[:72]}'"

        parsed.append(
            NewsHeadline(
                title=title,
                publisher=publisher,
                published_at=pub_str,
                link=link,
                summary=summary[:280],
                catalyst_match=catalyst if is_recent else None,
            )
        )

    return parsed, matched_reason


def is_near_earnings(earnings_dates: list[date], today: date | None = None) -> str | None:
    """Return ISO date string if an earnings date is within +/- 1 day of `today`."""
    ref = today or datetime.now(timezone.utc).date()
    for d in earnings_dates:
        if abs((d - ref).days) <= 1:
            return d.isoformat()
    return None


def _fetch_yf_snapshot_sync(
    symbol: str,
) -> tuple[list[NewsHeadline], str | None, list[date], str | None]:
    """Fetch Yahoo Finance headlines and upcoming earnings dates synchronously."""
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    raw_news = ticker.news or []
    headlines, catalyst_reason = classify_headlines(raw_news)

    earnings_dates: list[date] = []
    try:
        cal = ticker.calendar
        if isinstance(cal, dict) and "Earnings Date" in cal:
            vals = cal["Earnings Date"]
            if not isinstance(vals, list):
                vals = [vals]
            for v in vals:
                if hasattr(v, "date"):
                    earnings_dates.append(v.date())
                elif isinstance(v, date):
                    earnings_dates.append(v)
    except Exception:
        pass

    earnings_str = earnings_dates[0].isoformat() if earnings_dates else None
    return headlines, catalyst_reason, earnings_dates, earnings_str


def _build_llm_prompt(
    symbol: str,
    headlines: list[NewsHeadline],
    earnings_date: str | None,
    now_utc: datetime,
) -> str:
    lines = [
        f"Ticker: {symbol}",
        f"Current UTC time: {now_utc.strftime('%Y-%m-%d %H:%M UTC')}",
        f"Next/recent earnings date: {earnings_date or 'Unknown'}",
        "",
        "Recent headlines:",
    ]
    for idx, h in enumerate(headlines):
        meta = " · ".join(p for p in (h.publisher, h.published_at) if p)
        lines.append(f"[{idx}] {h.title}" + (f" ({meta})" if meta else ""))
        if h.summary:
            lines.append(f"    Summary: {h.summary}")
    return "\n".join(lines)


class NewsGuard:
    """Cached news & earnings-calendar veto check with Gemini LLM rationale."""

    def __init__(
        self,
        ttl_seconds: int = 900,
        api_key: str | None = None,
        model: str = "gemini-3.8-flash",
        use_llm: bool = True,
        llm_timeout_seconds: float = 8.0,
        llm_evaluator: LLMEvaluator | None = None,
        data_fetcher: Callable[
            [str], tuple[list[NewsHeadline], str | None, list[date], str | None]
        ]
        | None = None,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._api_key = (
            api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or None
        )
        self._model = model
        self._use_llm = use_llm
        self._llm_timeout_seconds = llm_timeout_seconds
        self._llm_evaluator = llm_evaluator
        self._data_fetcher = data_fetcher or _fetch_yf_snapshot_sync
        self._cache: dict[str, tuple[float, NewsCheckResult]] = {}

    @property
    def llm_enabled(self) -> bool:
        return self._use_llm and (
            self._llm_evaluator is not None or bool(self._api_key)
        )

    def peek_cached(self, symbol: str) -> NewsCheckResult | None:
        entry = self._cache.get(symbol.strip().upper())
        if entry is None:
            return None
        ts, res = entry
        if time.monotonic() - ts > self._ttl_seconds:
            return None
        return res

    async def _evaluate_with_gemini(
        self,
        symbol: str,
        headlines: list[NewsHeadline],
        earnings_date: str | None,
    ) -> NewsGuardLLMDecision:
        if self._llm_evaluator is not None:
            return await asyncio.wait_for(
                self._llm_evaluator(symbol, headlines, earnings_date),
                timeout=self._llm_timeout_seconds,
            )

        from google import genai

        prompt = _build_llm_prompt(
            symbol=symbol,
            headlines=headlines,
            earnings_date=earnings_date,
            now_utc=datetime.now(timezone.utc),
        )
        async with genai.Client(api_key=self._api_key).aio as aclient:
            interaction = await asyncio.wait_for(
                aclient.interactions.create(
                    model=self._model,
                    system_instruction=NEWS_GUARD_SYSTEM_INSTRUCTION,
                    input=prompt,
                    generation_config={"temperature": 0.1},
                    response_format={
                        "type": "text",
                        "mime_type": "application/json",
                        "schema": NewsGuardLLMDecision.model_json_schema(),
                    },
                    store=False,
                    timeout=self._llm_timeout_seconds,
                ),
                timeout=self._llm_timeout_seconds + 1.0,
            )
        if not interaction.output_text:
            raise ValueError("Empty response from Gemini")
        return NewsGuardLLMDecision.model_validate_json(interaction.output_text)

    async def check(self, symbol: str, force_refresh: bool = False) -> NewsCheckResult:
        sym = symbol.strip().upper()
        if not force_refresh:
            cached = self.peek_cached(sym)
            if cached is not None:
                return cached

        try:
            headlines, keyword_reason, earnings_dates, earnings_str = await asyncio.to_thread(
                self._data_fetcher, sym
            )
        except Exception as exc:
            result = NewsCheckResult(
                symbol=sym,
                blocked=False,
                reason=f"News check unavailable ({exc})",
                rationale=f"Could not fetch news feed ({exc}); allowing entry.",
                classification="CLEAR",
                analysis_source="heuristic",
            )
            self._cache[sym] = (time.monotonic(), result)
            return result

        # Hard safety check: always block within +/- 1 day of earnings regardless of LLM
        near_earn = is_near_earnings(earnings_dates)
        if near_earn:
            result = NewsCheckResult(
                symbol=sym,
                blocked=True,
                reason=f"Earnings blackout window ({near_earn})",
                rationale=(
                    f"{sym} has an earnings date ({near_earn}) within ±1 day. "
                    "Binary earnings gap risk overrides technical mean-reversion setups."
                ),
                classification="EARNINGS_BLACKOUT",
                confidence=1.0,
                analysis_source="heuristic",
                earnings_date=near_earn,
                headlines=headlines,
            )
            self._cache[sym] = (time.monotonic(), result)
            return result

        if self.llm_enabled and headlines:
            try:
                decision = await self._evaluate_with_gemini(sym, headlines, earnings_str)
                flagged = {
                    idx
                    for idx in decision.flagged_headline_indices
                    if 0 <= idx < len(headlines)
                }
                annotated_headlines = [
                    h.model_copy(
                        update={
                            "catalyst_match": "LLM: fundamental risk"
                            if (decision.blocked and idx in flagged)
                            else None
                        }
                    )
                    for idx, h in enumerate(headlines)
                ]
                short_reason = (
                    f"{decision.classification}: {decision.rationale[:140]}"
                    if decision.blocked
                    else f"Clear ({decision.classification}): {decision.rationale[:120]}"
                )
                result = NewsCheckResult(
                    symbol=sym,
                    blocked=decision.blocked,
                    reason=short_reason,
                    rationale=decision.rationale,
                    classification=decision.classification,
                    confidence=round(decision.confidence, 2),
                    analysis_source="gemini",
                    model=self._model,
                    earnings_date=earnings_str,
                    headlines=annotated_headlines,
                )
                self._cache[sym] = (time.monotonic(), result)
                return result
            except Exception as exc:
                fallback_note = f"Gemini rationale unavailable ({exc}); using keyword heuristic. "
        else:
            fallback_note = ""

        if keyword_reason:
            result = NewsCheckResult(
                symbol=sym,
                blocked=True,
                reason=keyword_reason,
                rationale=f"{fallback_note}Keyword filter flagged a recent catalyst: {keyword_reason}",
                classification="FUNDAMENTAL_RISK",
                analysis_source="heuristic",
                earnings_date=earnings_str,
                headlines=headlines,
            )
        else:
            clear_detail = (
                "No earnings blackout or fundamental catalyst keyword detected in recent headlines."
                if headlines
                else "No recent headlines returned; earnings calendar clear."
            )
            result = NewsCheckResult(
                symbol=sym,
                blocked=False,
                reason="Clear (no earnings blackout or fundamental catalyst headline)",
                rationale=f"{fallback_note}{clear_detail}",
                classification="CLEAR",
                analysis_source="heuristic",
                earnings_date=earnings_str,
                headlines=headlines,
            )

        self._cache[sym] = (time.monotonic(), result)
        return result
