from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import mean
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

POLYGON_BASE_URL = "https://api.polygon.io"
TAVILY_BASE_URL = "https://api.tavily.com"
ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_DATA_BASE_URL = "https://data.alpaca.markets"
DEFAULT_ACTIVE_UNIVERSE = [
    "NVDA",
    "AAPL",
    "MSFT",
    "AMZN",
    "META",
    "GOOGL",
    "AMD",
    "TSLA",
    "AVGO",
    "NFLX",
]
SECTOR_ETFS = {
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Technology": "XLK",
    "Utilities": "XLU",
}

ToolHandler = Callable[..., Awaitable[Any]]


class ToolRegistryError(RuntimeError):
    """Raised when a requested tool fails."""


@dataclass(slots=True)
class ToolContext:
    state: dict[str, Any] | None = None
    agent_name: str = "system"


class TradingToolRegistry:
    """Central registry for market, strategy, risk, and execution tools."""

    def __init__(self, polygon_api_key: str | None = None, timeout_seconds: float = 20.0) -> None:
        self.polygon_api_key = polygon_api_key or os.getenv("POLYGON_API_KEY")
        self.tavily_api_key = os.getenv("TAVILY_API_KEY")
        self.timeout_seconds = timeout_seconds
        self._tools: dict[str, ToolHandler] = {
            "get_stock_bars": self.get_stock_bars,
            "get_latest_quote": self.get_latest_quote,
            "get_most_actives": self.get_most_actives,
            "check_market_clock": self.check_market_clock,
            "get_stock_news": self.get_stock_news,
            "search_web_research": self.search_web_research,
            "calculate_rsi": self.calculate_rsi,
            "calculate_macd": self.calculate_macd,
            "get_sec_filing": self.get_sec_filing,
            "get_earnings_calendar": self.get_earnings_calendar,
            "get_vix_level": self.get_vix_level,
            "get_sector_performance": self.get_sector_performance,
            "get_option_chain": self.get_option_chain,
            "place_option_order": self.place_option_order,
            "get_crypto_bars": self.get_crypto_bars,
            "place_crypto_order": self.place_crypto_order,
            "place_market_order": self.place_market_order,
            "cancel_all_orders": self.cancel_all_orders,
            "close_all_positions": self.close_all_positions,
            "get_account_balance": self.get_account_balance,
            "get_open_positions": self.get_open_positions,
            "set_stop_loss_order": self.set_stop_loss_order,
            "get_portfolio_history": self.get_portfolio_history,
        }

    def catalog(self) -> dict[str, list[str]]:
        return {
            "market_data": [
                "get_stock_bars",
                "get_latest_quote",
                "get_most_actives",
                "check_market_clock",
                "get_stock_news",
                "search_web_research",
            ],
            "strategy": [
                "calculate_rsi",
                "calculate_macd",
                "get_sec_filing",
                "get_earnings_calendar",
                "get_vix_level",
                "get_sector_performance",
            ],
            "execution": [
                "get_option_chain",
                "place_option_order",
                "get_crypto_bars",
                "place_crypto_order",
                "place_market_order",
                "cancel_all_orders",
                "close_all_positions",
            ],
            "risk": [
                "get_account_balance",
                "get_open_positions",
                "set_stop_loss_order",
                "get_portfolio_history",
            ],
        }

    async def call_tool(self, tool_name: str, context: ToolContext | None = None, **kwargs: Any) -> Any:
        if tool_name not in self._tools:
            raise ToolRegistryError(f"Unsupported tool: {tool_name}")
        active_context = context or ToolContext()
        handler = self._tools[tool_name]
        try:
            result = await handler(**kwargs)
        except Exception as exc:
            self._record_invocation(active_context, tool_name, kwargs, {"error": str(exc)}, "error")
            raise
        self._record_invocation(active_context, tool_name, kwargs, result, "ok")
        return result

    def _record_invocation(
        self,
        context: ToolContext,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: Any,
        status: str,
    ) -> None:
        if context.state is None:
            return
        history = list(context.state.get("tool_history") or [])
        history.append(
            {
                "tool_name": tool_name,
                "agent": context.agent_name,
                "status": status,
                "input": tool_input,
                "output_preview": self._preview(tool_output),
            }
        )
        context.state["tool_history"] = history

    @staticmethod
    def _preview(value: Any) -> dict[str, Any] | list[Any] | str | None:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, list):
            return value[:3]
        if isinstance(value, dict):
            return {key: value[key] for key in list(value)[:8]}
        return str(value)

    async def get_stock_bars(self, ticker: str, timeframe: str = "day", limit: int = 60) -> dict[str, Any]:
        self._require_polygon()
        multiplier, timespan = self._normalize_timeframe(timeframe)
        end_date = datetime.now(tz=UTC).date()
        start_date = end_date - timedelta(days=max(limit * 3, 20))
        url = (
            f"{POLYGON_BASE_URL}/v2/aggs/ticker/{ticker.strip().upper()}/range/{multiplier}/{timespan}/"
            f"{start_date.isoformat()}/{end_date.isoformat()}"
        )
        payload = await self._polygon_get(
            url,
            {
                "adjusted": "true",
                "sort": "asc",
                "limit": max(limit, 10),
            },
        )
        results = payload.get("results") or []
        bars = [
            {
                "timestamp": bar.get("t"),
                "open": float(bar.get("o") or 0.0),
                "high": float(bar.get("h") or 0.0),
                "low": float(bar.get("l") or 0.0),
                "close": float(bar.get("c") or 0.0),
                "volume": float(bar.get("v") or 0.0),
            }
            for bar in results[-limit:]
        ]
        return {"ticker": ticker.strip().upper(), "timeframe": timeframe, "bars": bars}

    async def get_latest_quote(self, ticker: str) -> dict[str, Any]:
        self._require_polygon()
        url = f"{POLYGON_BASE_URL}/v2/last/nbbo/{ticker.strip().upper()}"
        payload = await self._polygon_get(url)
        result = payload.get("results") or {}
        return {
            "ticker": ticker.strip().upper(),
            "bid_price": float(result.get("P") or 0.0),
            "ask_price": float(result.get("p") or 0.0),
            "bid_size": float(result.get("S") or 0.0),
            "ask_size": float(result.get("s") or 0.0),
            "timestamp": result.get("t"),
        }

    async def get_most_actives(self, universe: list[str] | None = None, limit: int = 5) -> list[dict[str, Any]]:
        candidates = universe or self._resolve_active_universe()
        scored: list[dict[str, Any]] = []
        for ticker in candidates:
            bars = await self.get_stock_bars(ticker=ticker, limit=2)
            recent_bars = bars.get("bars") or []
            if not recent_bars:
                continue
            latest = recent_bars[-1]
            dollar_volume = float(latest["close"]) * float(latest["volume"])
            scored.append(
                {
                    "ticker": ticker,
                    "close": latest["close"],
                    "volume": latest["volume"],
                    "dollar_volume": dollar_volume,
                }
            )
        return sorted(scored, key=lambda item: item["dollar_volume"], reverse=True)[:limit]

    async def check_market_clock(self) -> dict[str, Any]:
        now = datetime.now(tz=UTC)
        is_weekday = now.weekday() < 5
        is_market_hours = 14 <= now.hour < 21
        return {
            "timestamp_utc": now.isoformat(),
            "is_open": bool(is_weekday and is_market_hours),
            "source": "derived_utc_session",
        }

    async def get_stock_news(self, ticker: str, limit: int = 5) -> list[dict[str, Any]]:
        self._require_polygon()
        payload = await self._polygon_get(
            f"{POLYGON_BASE_URL}/v2/reference/news",
            {"ticker": ticker.strip().upper(), "limit": limit, "order": "desc", "sort": "published_utc"},
        )
        results = payload.get("results") or []
        return [
            {
                "headline": item.get("title"),
                "summary": item.get("description"),
                "published_utc": item.get("published_utc"),
                "article_url": item.get("article_url"),
            }
            for item in results[:limit]
        ]

    async def search_web_research(self, query: str, limit: int = 5) -> dict[str, Any]:
        self._require_tavily()
        payload = await self._json_post(
            f"{TAVILY_BASE_URL}/search",
            json={
                "api_key": self.tavily_api_key,
                "query": query,
                "search_depth": "advanced",
                "topic": "news",
                "max_results": max(1, min(limit, 8)),
                "include_answer": True,
                "include_raw_content": False,
            },
        )
        results = payload.get("results") or []
        return {
            "query": query,
            "answer": payload.get("answer"),
            "results": [
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "content": item.get("content"),
                    "score": item.get("score"),
                    "published_date": item.get("published_date"),
                }
                for item in results[:limit]
            ],
        }

    async def calculate_rsi(self, ticker: str, period: int = 14) -> dict[str, Any]:
        closes = await self._fetch_close_series(ticker=ticker, limit=max(period + 20, 40))
        if len(closes) <= period:
            raise ToolRegistryError(f"Not enough data to calculate RSI for {ticker}")
        gains: list[float] = []
        losses: list[float] = []
        for previous, current in zip(closes, closes[1:]):
            delta = current - previous
            gains.append(max(delta, 0.0))
            losses.append(abs(min(delta, 0.0)))
        avg_gain = mean(gains[-period:]) if any(gains[-period:]) else 0.0
        avg_loss = mean(losses[-period:]) if any(losses[-period:]) else 0.0
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        return {"ticker": ticker.strip().upper(), "period": period, "rsi": round(rsi, 2)}

    async def calculate_macd(
        self,
        ticker: str,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
    ) -> dict[str, Any]:
        closes = await self._fetch_close_series(ticker=ticker, limit=max(slow_period + signal_period + 40, 80))
        fast_ema = self._ema(closes, fast_period)
        slow_ema = self._ema(closes, slow_period)
        macd_line = [fast - slow for fast, slow in zip(fast_ema[-len(slow_ema):], slow_ema)]
        signal_line = self._ema(macd_line, signal_period)
        histogram = macd_line[-1] - signal_line[-1]
        return {
            "ticker": ticker.strip().upper(),
            "macd": round(macd_line[-1], 4),
            "signal": round(signal_line[-1], 4),
            "histogram": round(histogram, 4),
        }

    async def get_sec_filing(self, ticker: str) -> dict[str, Any]:
        mapping = await self._json_get("https://www.sec.gov/files/company_tickers.json", headers=self._sec_headers())
        ticker_upper = ticker.strip().upper()
        match = next((row for row in mapping.values() if str(row.get("ticker", "")).upper() == ticker_upper), None)
        if match is None:
            raise ToolRegistryError(f"SEC CIK lookup failed for {ticker_upper}")
        cik = str(match["cik_str"]).zfill(10)
        submissions = await self._json_get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers=self._sec_headers(),
        )
        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accession_numbers = recent.get("accessionNumber") or []
        primary_docs = recent.get("primaryDocument") or []
        if not forms:
            raise ToolRegistryError(f"No recent SEC filings found for {ticker_upper}")
        accession = str(accession_numbers[0]).replace("-", "")
        primary_doc = primary_docs[0]
        return {
            "ticker": ticker_upper,
            "company_name": match.get("title"),
            "form": forms[0],
            "filing_date": dates[0],
            "filing_url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{primary_doc}",
        }

    async def get_earnings_calendar(self, ticker: str) -> dict[str, Any]:
        self._require_polygon()
        payload = await self._polygon_get(
            f"{POLYGON_BASE_URL}/vX/reference/financials",
            {"ticker": ticker.strip().upper(), "limit": 1, "order": "desc", "sort": "filing_date"},
        )
        results = payload.get("results") or []
        if not results:
            return {"ticker": ticker.strip().upper(), "status": "unavailable"}
        financial = results[0]
        return {
            "ticker": ticker.strip().upper(),
            "filing_date": financial.get("filing_date"),
            "fiscal_period": financial.get("fiscal_period"),
            "fiscal_year": financial.get("fiscal_year"),
            "status": "ok",
        }

    async def get_vix_level(self) -> dict[str, Any]:
        bars = await self.get_stock_bars(ticker="I:VIX", limit=1)
        latest = (bars.get("bars") or [{}])[-1]
        return {"symbol": "I:VIX", "price": latest.get("close"), "timestamp": latest.get("timestamp")}

    async def get_sector_performance(self) -> list[dict[str, Any]]:
        performance: list[dict[str, Any]] = []
        for sector, ticker in SECTOR_ETFS.items():
            bars = await self.get_stock_bars(ticker=ticker, limit=2)
            series = bars.get("bars") or []
            if len(series) < 2:
                continue
            previous_close = float(series[-2]["close"])
            latest_close = float(series[-1]["close"])
            change_pct = ((latest_close / previous_close) - 1.0) * 100 if previous_close else 0.0
            performance.append(
                {
                    "sector": sector,
                    "proxy": ticker,
                    "change_pct": round(change_pct, 2),
                }
            )
        return sorted(performance, key=lambda item: item["change_pct"], reverse=True)

    async def get_option_chain(self, underlying_symbol: str, expiration_date: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        self._require_polygon()
        params: dict[str, Any] = {"underlying_ticker": underlying_symbol.strip().upper(), "limit": limit}
        if expiration_date:
            params["expiration_date"] = expiration_date
        payload = await self._polygon_get(f"{POLYGON_BASE_URL}/v3/reference/options/contracts", params)
        return payload.get("results") or []

    async def place_option_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        broker_connection: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._alpaca_order(
            broker_connection=broker_connection,
            payload={
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "type": "market",
                "time_in_force": "day",
            },
        )

    async def get_crypto_bars(self, symbol: str, timeframe: str = "1Day", limit: int = 50) -> dict[str, Any]:
        normalized_symbol = symbol.strip().upper().replace("/", "")
        url = f"{ALPACA_DATA_BASE_URL}/v1beta3/crypto/us/bars"
        payload = await self._alpaca_data_get(
            url,
            {"symbols": normalized_symbol, "timeframe": timeframe, "limit": limit},
        )
        return {
            "symbol": normalized_symbol,
            "bars": payload.get("bars", {}).get(normalized_symbol, []),
        }

    async def place_crypto_order(
        self,
        symbol: str,
        notional: float,
        side: str,
        broker_connection: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._alpaca_order(
            broker_connection=broker_connection,
            payload={
                "symbol": symbol.strip().upper().replace("/", ""),
                "notional": notional,
                "side": side,
                "type": "market",
                "time_in_force": "day",
            },
        )

    async def place_market_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        broker_connection: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._alpaca_order(
            broker_connection=broker_connection,
            payload={
                "symbol": symbol.strip().upper(),
                "qty": qty,
                "side": side,
                "type": "market",
                "time_in_force": "day",
            },
        )

    async def cancel_all_orders(self, broker_connection: dict[str, Any]) -> dict[str, Any]:
        return await self._alpaca_delete("/v2/orders", broker_connection)

    async def close_all_positions(self, broker_connection: dict[str, Any]) -> dict[str, Any]:
        return await self._alpaca_delete("/v2/positions", broker_connection)

    async def get_account_balance(self, broker_connection: dict[str, Any]) -> dict[str, Any]:
        return await self._alpaca_get("/v2/account", broker_connection)

    async def get_open_positions(self, broker_connection: dict[str, Any]) -> list[dict[str, Any]]:
        payload = await self._alpaca_get("/v2/positions", broker_connection)
        return payload if isinstance(payload, list) else []

    async def set_stop_loss_order(self, symbol: str, qty: int, stop_price: float, broker_connection: dict[str, Any]) -> dict[str, Any]:
        return await self._alpaca_order(
            broker_connection=broker_connection,
            payload={
                "symbol": symbol.strip().upper(),
                "qty": qty,
                "side": "sell",
                "type": "stop",
                "time_in_force": "gtc",
                "stop_price": round(stop_price, 2),
            },
        )

    async def get_portfolio_history(self, broker_connection: dict[str, Any], period: str = "1M") -> dict[str, Any]:
        return await self._alpaca_get("/v2/account/portfolio/history", broker_connection, {"period": period})

    async def _fetch_close_series(self, ticker: str, limit: int) -> list[float]:
        bars = await self.get_stock_bars(ticker=ticker, limit=limit)
        return [float(bar["close"]) for bar in bars.get("bars") or [] if bar.get("close") is not None]

    @staticmethod
    def _ema(values: list[float], period: int) -> list[float]:
        if not values:
            return []
        multiplier = 2 / (period + 1)
        ema_values = [values[0]]
        for value in values[1:]:
            ema_values.append((value - ema_values[-1]) * multiplier + ema_values[-1])
        return ema_values

    @staticmethod
    def _normalize_timeframe(timeframe: str) -> tuple[int, str]:
        normalized = timeframe.strip().lower()
        mapping = {
            "minute": (1, "minute"),
            "1min": (1, "minute"),
            "hour": (1, "hour"),
            "1hour": (1, "hour"),
            "day": (1, "day"),
            "1day": (1, "day"),
        }
        return mapping.get(normalized, (1, "day"))

    def _require_polygon(self) -> None:
        if not self.polygon_api_key:
            raise ToolRegistryError("POLYGON_API_KEY is required for this tool")

    def _require_tavily(self) -> None:
        if not self.tavily_api_key:
            raise ToolRegistryError("TAVILY_API_KEY is required for Tavily web research")

    async def _polygon_get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = dict(params or {})
        query["apiKey"] = self.polygon_api_key
        return await self._json_get(url, params=query)

    async def _alpaca_get(
        self,
        path: str,
        broker_connection: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = await self._request_with_credentials("GET", f"{ALPACA_PAPER_BASE_URL}{path}", broker_connection, params=params)
        return response.json()

    async def _alpaca_delete(self, path: str, broker_connection: dict[str, Any]) -> dict[str, Any]:
        response = await self._request_with_credentials("DELETE", f"{ALPACA_PAPER_BASE_URL}{path}", broker_connection)
        if response.text.strip():
            return response.json()
        return {"status": "ok"}

    async def _alpaca_order(self, broker_connection: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._request_with_credentials(
            "POST",
            f"{ALPACA_PAPER_BASE_URL}/v2/orders",
            broker_connection,
            json=payload,
        )
        return response.json()

    async def _alpaca_data_get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")
        if not api_key or not secret_key:
            raise ToolRegistryError("ALPACA_API_KEY and ALPACA_SECRET_KEY are required for Alpaca data tools")
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                url,
                params=params,
                headers={
                    "APCA-API-KEY-ID": api_key,
                    "APCA-API-SECRET-KEY": secret_key,
                },
            )
        self._raise_for_status(response, "Alpaca data request failed")
        return response.json()

    async def _request_with_credentials(
        self,
        method: str,
        url: str,
        broker_connection: dict[str, Any],
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        headers = {
            "APCA-API-KEY-ID": broker_connection["api_key"],
            "APCA-API-SECRET-KEY": broker_connection["secret_key"],
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.request(method, url, headers=headers, params=params, json=json)
        self._raise_for_status(response, f"Alpaca request failed: {url}")
        return response

    async def _json_get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        merged_headers = {"Accept": "application/json", **(headers or {})}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url, params=params, headers=merged_headers)
        self._raise_for_status(response, f"Request failed: {url}")
        return response.json()

    async def _json_post(
        self,
        url: str,
        json: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        merged_headers = {"Accept": "application/json", "Content-Type": "application/json", **(headers or {})}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(url, json=json, headers=merged_headers)
        self._raise_for_status(response, f"Request failed: {url}")
        return response.json()

    @staticmethod
    def _raise_for_status(response: httpx.Response, prefix: str) -> None:
        if response.status_code >= 400:
            raise ToolRegistryError(f"{prefix} status={response.status_code} body={response.text[:300]}")

    @staticmethod
    def _resolve_active_universe() -> list[str]:
        raw_value = os.getenv("MOST_ACTIVE_UNIVERSE", "")
        parsed = [ticker.strip().upper() for ticker in raw_value.split(",") if ticker.strip()]
        return parsed or list(DEFAULT_ACTIVE_UNIVERSE)

    @staticmethod
    def _sec_headers() -> dict[str, str]:
        user_agent = os.getenv("SEC_USER_AGENT", "multi-agent-equity-trading-platform support@example.com")
        return {"User-Agent": user_agent}
