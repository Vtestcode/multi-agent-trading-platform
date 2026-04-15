from __future__ import annotations

import asyncio
import logging
import os
import random
from dataclasses import dataclass, asdict
from typing import Any

from agents.market_data_agent import MarketDataAgent, MarketDataError

logger = logging.getLogger(__name__)

DEFAULT_AUTO_TRADE_UNIVERSE = [
    "NVDA",
    "MSFT",
    "AAPL",
    "AMZN",
    "META",
    "GOOGL",
    "AVGO",
    "TSLA",
    "AMD",
    "NFLX",
    "PLTR",
    "JPM",
    "LLY",
    "COST",
    "CRM",
    "ORCL",
    "ADBE",
    "INTU",
    "QCOM",
    "TXN",
    "AMAT",
    "MU",
    "PANW",
    "CRWD",
    "SHOP",
    "UBER",
    "ABNB",
    "BKNG",
    "DIS",
    "WMT",
    "HD",
    "LOW",
    "NKE",
    "MCD",
    "SBUX",
    "XOM",
    "CVX",
    "UNH",
    "ABBV",
    "PFE",
    "MRK",
    "GS",
    "BAC",
    "KO",
    "PEP",
]
DEFAULT_SCAN_SAMPLE_SIZE = 2
DEFAULT_SCAN_CONCURRENCY = 1


@dataclass(slots=True)
class ScanCandidate:
    ticker: str
    momentum_score: float
    current_price: float
    sma_50: float
    sma_200: float
    avg_daily_volume: float
    latest_close_date: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MarketScannerAgent:
    """Scans a liquid universe and selects the strongest momentum candidate."""

    def __init__(self, market_data_agent: MarketDataAgent, max_candidates: int = 5) -> None:
        self.market_data_agent = market_data_agent
        self.max_candidates = max_candidates

    async def run(self, manual_ticker: str | None = None, excluded_tickers: list[str] | None = None) -> dict[str, Any]:
        if manual_ticker:
            ticker = manual_ticker.strip().upper()
            logger.info("[MarketScannerAgent] Manual ticker override for %s", ticker)
            snapshot_state = await self.market_data_agent.run(ticker=ticker)
            market_data = snapshot_state["market_data"]
            candidate = self._to_candidate(ticker=ticker, market_data=market_data)
            return {
                "selected_ticker": ticker,
                "scanner_mode": "manual",
                "scan_candidates": [candidate.to_dict()],
                "scanner_summary": f"Manual override used. Selected {ticker} directly.",
                "market_data": market_data,
            }

        universe = self._resolve_universe()
        logger.info("[MarketScannerAgent] Scanning %s candidate tickers", len(universe))
        snapshots, failures = await self._fetch_universe(universe)
        if not snapshots:
            full_universe = self._resolve_universe(full_scan=True)
            if full_universe != universe:
                logger.info("[MarketScannerAgent] Retrying scan across full universe of %s names", len(full_universe))
                snapshots, failures = await self._fetch_universe(full_universe)
        ranked = sorted(
            (self._to_candidate(ticker=ticker, market_data=market_data) for ticker, market_data in snapshots),
            key=lambda candidate: candidate.momentum_score,
            reverse=True,
        )
        if not ranked:
            failure_summary = "; ".join(f"{ticker}: {detail}" for ticker, detail in failures[:5]) if failures else "No market data was returned."
            raise MarketDataError(
                "Scanner could not find any valid candidates in the configured universe. "
                f"Recent failures: {failure_summary}"
            )

        excluded = {ticker.strip().upper() for ticker in (excluded_tickers or []) if ticker}
        top_candidates = ranked[: self.max_candidates]
        selected = next((candidate for candidate in ranked if candidate.ticker not in excluded), ranked[0])
        if selected not in top_candidates:
            top_candidates = [selected, *[candidate for candidate in top_candidates if candidate.ticker != selected.ticker]][
                : self.max_candidates
            ]
        logger.info(
            "[MarketScannerAgent] Selected %s momentum_score=%.4f from %s scanned names",
            selected.ticker,
            selected.momentum_score,
            len(ranked),
        )
        if excluded and selected.ticker not in excluded:
            summary = (
                f"Auto-selected {selected.ticker} as the strongest eligible momentum candidate from {len(ranked)} valid names. "
                f"Avoided recent picks: {', '.join(sorted(excluded))}."
            )
        else:
            summary = f"Auto-selected {selected.ticker} as the strongest momentum candidate from {len(ranked)} valid names."
        return {
            "selected_ticker": selected.ticker,
            "scanner_mode": "auto",
            "scan_candidates": [candidate.to_dict() for candidate in top_candidates],
            "scanner_summary": summary,
            "market_data": next(market_data for ticker, market_data in snapshots if ticker == selected.ticker),
        }

    async def _fetch_universe(self, universe: list[str]) -> tuple[list[tuple[str, dict[str, Any]]], list[tuple[str, str]]]:
        semaphore = asyncio.Semaphore(self._scan_concurrency())
        failures: list[tuple[str, str]] = []

        async def fetch_one(ticker: str) -> tuple[str, dict[str, Any]] | None:
            async with semaphore:
                try:
                    result = await self.market_data_agent.run(ticker=ticker)
                    return ticker, result["market_data"]
                except Exception as exc:
                    logger.warning("[MarketScannerAgent] Skipping %s during scan: %s", ticker, exc)
                    failures.append((ticker, str(exc)))
                    return None

        results = await asyncio.gather(*(fetch_one(ticker) for ticker in universe))
        return [item for item in results if item is not None], failures

    @staticmethod
    def _to_candidate(ticker: str, market_data: dict[str, Any]) -> ScanCandidate:
        current_price = float(market_data["current_price"])
        sma_50 = float(market_data["sma_50"])
        sma_200 = float(market_data["sma_200"])
        avg_daily_volume = float(market_data["avg_daily_volume"])
        momentum_score = (
            ((current_price / sma_50) - 1.0) * 0.45
            + ((sma_50 / sma_200) - 1.0) * 0.45
            + min(avg_daily_volume / 5_000_000, 1.0) * 0.10
        )
        summary = (
            f"{ticker} price {current_price:.2f}, SMA50 {sma_50:.2f}, SMA200 {sma_200:.2f}, "
            f"avg volume {avg_daily_volume:,.0f}."
        )
        return ScanCandidate(
            ticker=ticker,
            momentum_score=momentum_score,
            current_price=current_price,
            sma_50=sma_50,
            sma_200=sma_200,
            avg_daily_volume=avg_daily_volume,
            latest_close_date=str(market_data["latest_close_date"]),
            summary=summary,
        )

    @staticmethod
    def _resolve_universe(full_scan: bool = False) -> list[str]:
        env_value = os.getenv("AUTO_TRADE_UNIVERSE")
        sample_size = MarketScannerAgent._scan_sample_size()
        if env_value:
            parsed = [ticker.strip().upper() for ticker in env_value.split(",") if ticker.strip()]
            if parsed:
                return parsed if full_scan else parsed[:sample_size]
        if len(DEFAULT_AUTO_TRADE_UNIVERSE) <= sample_size or full_scan:
            return list(DEFAULT_AUTO_TRADE_UNIVERSE)
        return random.sample(DEFAULT_AUTO_TRADE_UNIVERSE, sample_size)

    @staticmethod
    def _scan_sample_size() -> int:
        raw_value = os.getenv("AUTO_SCAN_SAMPLE_SIZE", str(DEFAULT_SCAN_SAMPLE_SIZE)).strip()
        try:
            parsed = int(raw_value)
        except ValueError:
            parsed = DEFAULT_SCAN_SAMPLE_SIZE
        return max(1, min(parsed, len(DEFAULT_AUTO_TRADE_UNIVERSE)))

    @staticmethod
    def _scan_concurrency() -> int:
        raw_value = os.getenv("AUTO_SCAN_CONCURRENCY", str(DEFAULT_SCAN_CONCURRENCY)).strip()
        try:
            parsed = int(raw_value)
        except ValueError:
            parsed = DEFAULT_SCAN_CONCURRENCY
        return max(1, min(parsed, 3))
