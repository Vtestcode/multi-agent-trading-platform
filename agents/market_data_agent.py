from __future__ import annotations

import logging
import os
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from statistics import mean
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)

POLYGON_BASE_URL = "https://api.polygon.io"
DEFAULT_LOOKBACK_DAYS = 320


class MarketDataError(RuntimeError):
    """Raised when market data cannot be fetched or validated."""


@dataclass(slots=True)
class MarketSnapshot:
    ticker: str
    current_price: float
    sma_50: float
    sma_200: float
    avg_daily_volume: float
    latest_volume: float
    latest_close_date: str
    source: str = "polygon_rest"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MarketDataAgent:
    """Fetches equity market data required by the momentum strategy."""

    def __init__(
        self,
        api_key: str | None = None,
        timeout_seconds: float = 20.0,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    ) -> None:
        self.api_key = api_key or os.getenv("POLYGON_API_KEY")
        if not self.api_key:
            raise ValueError("POLYGON_API_KEY is required for MarketDataAgent")

        self.timeout_seconds = timeout_seconds
        self.lookback_days = lookback_days

    async def run(self, ticker: str) -> Dict[str, Any]:
        ticker = ticker.strip().upper()
        logger.info("[MarketDataAgent] Fetching Polygon data for %s", ticker)

        bars = await self._fetch_daily_bars(ticker)
        snapshot = self._build_snapshot(ticker=ticker, bars=bars)

        logger.info(
            "[MarketDataAgent] %s current_price=%.4f sma50=%.4f sma200=%.4f avg_daily_volume=%.2f",
            ticker,
            snapshot.current_price,
            snapshot.sma_50,
            snapshot.sma_200,
            snapshot.avg_daily_volume,
        )
        return {"market_data": snapshot.to_dict()}

    async def _fetch_daily_bars(self, ticker: str) -> List[Dict[str, Any]]:
        end_date = date.today()
        start_date = end_date - timedelta(days=self.lookback_days)

        url = (
            f"{POLYGON_BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/"
            f"{start_date.isoformat()}/{end_date.isoformat()}"
        )
        params = {
            "adjusted": "true",
            "sort": "asc",
            "limit": 5000,
            "apiKey": self.api_key,
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url, params=params)

        if response.status_code != 200:
            raise MarketDataError(
                f"Polygon request failed with status={response.status_code} body={response.text[:300]}"
            )

        payload = response.json()
        results = payload.get("results") or []
        if len(results) < 200:
            raise MarketDataError(
                f"Not enough historical bars for {ticker}. Required >= 200, got {len(results)}."
            )

        valid_results = [bar for bar in results if bar.get("c") is not None and bar.get("v") is not None]
        if len(valid_results) < 200:
            raise MarketDataError(
                f"Not enough valid bars for {ticker}. Required >= 200, got {len(valid_results)}."
            )

        return valid_results

    def _build_snapshot(self, ticker: str, bars: List[Dict[str, Any]]) -> MarketSnapshot:
        closes = [float(bar["c"]) for bar in bars]
        volumes = [float(bar["v"]) for bar in bars]
        latest_bar = bars[-1]

        sma_50 = mean(closes[-50:])
        sma_200 = mean(closes[-200:])
        avg_daily_volume = mean(volumes[-50:])
        current_price = float(latest_bar["c"])
        latest_volume = float(latest_bar["v"])
        latest_close_date = self._polygon_timestamp_to_date(latest_bar["t"])

        return MarketSnapshot(
            ticker=ticker,
            current_price=current_price,
            sma_50=sma_50,
            sma_200=sma_200,
            avg_daily_volume=avg_daily_volume,
            latest_volume=latest_volume,
            latest_close_date=latest_close_date,
        )

    @staticmethod
    def _polygon_timestamp_to_date(ts_ms: int) -> str:
        # Polygon timestamps are epoch milliseconds.
        return date.fromtimestamp(ts_ms / 1000).isoformat()
