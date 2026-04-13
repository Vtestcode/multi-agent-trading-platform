from __future__ import annotations

import logging
from typing import Any, Dict, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from agents.llm_common import compact_json, resolve_model_name
from agents.tool_registry import ToolContext, TradingToolRegistry

logger = logging.getLogger(__name__)


class ResearchBrief(BaseModel):
    summary: str = Field(min_length=20)
    sentiment: Literal["bullish", "bearish", "mixed", "neutral"]
    current_updates: list[str] = Field(default_factory=list)
    catalysts: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)


class ResearchAgent:
    """Builds a current-market research brief with Tavily web search before strategy and risk decisions."""

    def __init__(self, model_name: str | None = None, tool_registry: TradingToolRegistry | None = None) -> None:
        self.model_name = resolve_model_name(model_name)
        self.tool_registry = tool_registry or TradingToolRegistry()
        self.agent = Agent(
            self.model_name,
            output_type=ResearchBrief,
            instructions=(
                "You are a market research analyst for a US equities trading platform. "
                "Use the supplied current-market inputs to summarize what matters right now for the selected ticker. "
                "Prioritize recency, explainability, and execution-relevant context over broad commentary. "
                "Identify near-term catalysts, major risk flags, and the overall market read as bullish, bearish, mixed, or neutral."
            ),
        )

    async def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        ticker = state.get("ticker", "UNKNOWN")
        market_data = state.get("market_data") or {}
        research_inputs = await self._gather_inputs(state, ticker)
        prompt = self._build_prompt(ticker=ticker, market_data=market_data, research_inputs=research_inputs)

        result = await self.agent.run(prompt)
        brief = result.output

        logger.info("[ResearchAgent] %s sentiment=%s", ticker, brief.sentiment)
        logger.info("[ResearchAgent] %s", brief.summary)

        return {
            "research_summary": brief.summary,
            "research_sentiment": brief.sentiment,
            "research_updates": brief.current_updates,
            "research_catalysts": brief.catalysts,
            "research_risk_flags": brief.risk_flags,
            "research_inputs": research_inputs,
            "decision_model": self.model_name,
        }

    async def _gather_inputs(self, state: Dict[str, Any], ticker: str) -> Dict[str, Any]:
        context = ToolContext(state=state, agent_name="research")
        inputs: Dict[str, Any] = {}
        inputs["check_market_clock"] = await self._call_best_effort(
            "check_market_clock",
            context=context,
        )
        inputs["get_latest_quote"] = await self._get_quote_with_fallback(
            state=state,
            ticker=ticker,
            context=context,
        )
        inputs["get_stock_news"] = await self._call_best_effort(
            "get_stock_news",
            context=context,
            ticker=ticker,
            limit=5,
        )
        inputs["search_web_research"] = await self.tool_registry.call_tool(
            "search_web_research",
            context=context,
            query=f"{ticker} stock latest news catalysts risks analyst updates today",
            limit=5,
        )
        inputs["get_earnings_calendar"] = await self._call_best_effort(
            "get_earnings_calendar",
            context=context,
            ticker=ticker,
        )
        inputs["get_vix_level"] = await self._call_best_effort(
            "get_vix_level",
            context=context,
        )
        inputs["get_sector_performance"] = await self._call_best_effort(
            "get_sector_performance",
            context=context,
        )
        inputs["get_most_actives"] = await self._call_best_effort(
            "get_most_actives",
            context=context,
            limit=5,
        )
        return inputs

    async def _call_best_effort(
        self,
        tool_name: str,
        context: ToolContext,
        **kwargs: Any,
    ) -> Any:
        try:
            return await self.tool_registry.call_tool(tool_name, context=context, **kwargs)
        except Exception as exc:
            logger.warning("[ResearchAgent] %s failed for %s: %s", tool_name, kwargs.get("ticker", "market"), exc)
            return {"status": "unavailable", "detail": str(exc)}

    async def _get_quote_with_fallback(
        self,
        state: Dict[str, Any],
        ticker: str,
        context: ToolContext,
    ) -> Dict[str, Any]:
        try:
            return await self.tool_registry.call_tool("get_latest_quote", context=context, ticker=ticker)
        except Exception as exc:
            logger.warning("[ResearchAgent] get_latest_quote failed for %s: %s", ticker, exc)
            fallback_quote = self._quote_from_market_data(state, ticker)
            if fallback_quote:
                fallback_quote["detail"] = str(exc)
                return fallback_quote
            return {"status": "unavailable", "detail": str(exc)}

    @staticmethod
    def _quote_from_market_data(state: Dict[str, Any], ticker: str) -> Dict[str, Any] | None:
        market_data = state.get("market_data") or {}
        if str(market_data.get("ticker") or "").upper() != ticker.upper():
            return None

        current_price = market_data.get("current_price")
        if current_price is None:
            return None

        recent_bars = market_data.get("recent_bars") or []
        latest_bar = recent_bars[-1] if recent_bars else {}
        return {
            "ticker": ticker.upper(),
            "last_price": float(current_price),
            "close_price": float(current_price),
            "timestamp": latest_bar.get("date") or market_data.get("latest_close_date"),
            "source": "market_data_fallback",
            "status": "fallback",
        }

    @staticmethod
    def _build_prompt(ticker: str, market_data: Dict[str, Any], research_inputs: Dict[str, Any]) -> str:
        return (
            "Build a current-market research brief for this ticker.\n\n"
            f"Ticker: {ticker}\n"
            "Market snapshot JSON:\n"
            f"{compact_json(market_data)}\n\n"
            "Current-market inputs JSON:\n"
            f"{compact_json(research_inputs)}\n\n"
            "Research guidance:\n"
            "- Focus on what is current and decision-relevant.\n"
            "- Use Tavily web results to capture fresh market updates around the ticker.\n"
            "- Distinguish catalysts from risks.\n"
            "- Keep the summary concise and institutional.\n"
            "- Use mixed or neutral sentiment when evidence is not one-sided."
        )
