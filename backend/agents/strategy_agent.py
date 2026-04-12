from __future__ import annotations

import logging
from typing import Any, Dict, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from agents.llm_common import compact_json, resolve_model_name
from agents.tool_registry import ToolContext, TradingToolRegistry

logger = logging.getLogger(__name__)


class StrategyDecision(BaseModel):
    signal: Literal["BUY", "SELL", "HOLD"]
    rationale: str = Field(min_length=12)
    confidence: float = Field(ge=0.0, le=1.0)
    risks: list[str] = Field(default_factory=list)


class StrategyAgent:
    """Uses an LLM as the strategy brain and returns a typed trading decision."""

    def __init__(self, model_name: str | None = None, tool_registry: TradingToolRegistry | None = None) -> None:
        self.model_name = resolve_model_name(model_name)
        self.tool_registry = tool_registry or TradingToolRegistry()
        self.agent = Agent(
            self.model_name,
            output_type=StrategyDecision,
            instructions=(
                "You are the strategy brain for a US equities trading platform. "
                "Review the supplied market snapshot and choose exactly one of BUY, SELL, or HOLD. "
                "Favor disciplined, explainable decisions over aggressive trading. "
                "Use the moving averages, price action, and volume context to make the decision. "
                "Return concise institutional-grade reasoning and a confidence score between 0 and 1."
            ),
        )

    async def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        market_data = state.get("market_data") or {}
        ticker = state.get("ticker", "UNKNOWN")
        strategy_inputs = await self._gather_inputs(state, ticker)
        prompt = self._build_prompt(ticker=ticker, market_data=market_data, strategy_inputs=strategy_inputs)

        result = await self.agent.run(prompt)
        decision = result.output

        logger.info("[StrategyAgent] %s signal=%s confidence=%.2f", ticker, decision.signal, decision.confidence)
        logger.info("[StrategyAgent] %s", decision.rationale)

        return {
            "signal": decision.signal,
            "strategy_reason": decision.rationale,
            "strategy_confidence": decision.confidence,
            "strategy_risks": decision.risks,
            "strategy_inputs": strategy_inputs,
            "decision_model": self.model_name,
        }

    async def _gather_inputs(self, state: Dict[str, Any], ticker: str) -> Dict[str, Any]:
        context = ToolContext(state=state, agent_name="strategy")
        inputs: Dict[str, Any] = {}
        tool_calls = [
            ("calculate_rsi", {"ticker": ticker}),
            ("calculate_macd", {"ticker": ticker}),
            ("get_stock_news", {"ticker": ticker, "limit": 3}),
            ("get_sec_filing", {"ticker": ticker}),
            ("get_earnings_calendar", {"ticker": ticker}),
            ("get_vix_level", {}),
            ("get_sector_performance", {}),
        ]
        for tool_name, kwargs in tool_calls:
            try:
                inputs[tool_name] = await self.tool_registry.call_tool(tool_name, context=context, **kwargs)
            except Exception as exc:
                logger.warning("[StrategyAgent] %s failed for %s: %s", tool_name, ticker, exc)
                inputs[tool_name] = {"status": "unavailable", "detail": str(exc)}
        return inputs

    @staticmethod
    def _build_prompt(ticker: str, market_data: Dict[str, Any], strategy_inputs: Dict[str, Any]) -> str:
        return (
            "Evaluate this market snapshot and produce a trading signal.\n\n"
            f"Ticker: {ticker}\n"
            "Market data JSON:\n"
            f"{compact_json(market_data)}\n\n"
            "Additional strategy inputs JSON:\n"
            f"{compact_json(strategy_inputs)}\n\n"
            "Decision guidance:\n"
            "- BUY when bullish evidence is strong and well supported.\n"
            "- SELL when trend deterioration is clear.\n"
            "- HOLD when evidence is mixed, weak, or insufficient.\n"
            "- Mention key risks that could invalidate the view."
        )
