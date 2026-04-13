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
        tool_calls = [
            ("check_market_clock", {}),
            ("get_latest_quote", {"ticker": ticker}),
            ("get_stock_news", {"ticker": ticker, "limit": 5}),
            (
                "search_web_research",
                {
                    "query": f"{ticker} stock latest news catalysts risks analyst updates today",
                    "limit": 5,
                },
            ),
            ("get_earnings_calendar", {"ticker": ticker}),
            ("get_vix_level", {}),
            ("get_sector_performance", {}),
            ("get_most_actives", {"limit": 5}),
        ]
        for tool_name, kwargs in tool_calls:
            inputs[tool_name] = await self.tool_registry.call_tool(tool_name, context=context, **kwargs)
        return inputs

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
