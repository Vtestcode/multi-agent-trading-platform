from __future__ import annotations

import logging
import math
from typing import Any, Dict

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from agents.llm_common import compact_json, resolve_model_name
from agents.tool_registry import ToolContext, TradingToolRegistry

logger = logging.getLogger(__name__)

ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
DEFAULT_MAX_POSITION_PCT = 0.05
DEFAULT_MIN_AVG_DAILY_VOLUME = 1_000_000


class RiskError(RuntimeError):
    """Raised when account or risk validation fails."""


class RiskDecision(BaseModel):
    approved: bool
    reason: str = Field(min_length=12)
    share_count: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)
    controls_triggered: list[str] = Field(default_factory=list)


class RiskAgent:
    """Uses an LLM for risk judgement, then enforces deterministic trade guardrails."""

    def __init__(
        self,
        paper_base_url: str = ALPACA_PAPER_BASE_URL,
        max_position_pct: float = DEFAULT_MAX_POSITION_PCT,
        min_avg_daily_volume: int = DEFAULT_MIN_AVG_DAILY_VOLUME,
        timeout_seconds: float = 20.0,
        model_name: str | None = None,
        tool_registry: TradingToolRegistry | None = None,
    ) -> None:
        self.paper_base_url = paper_base_url.rstrip("/")
        self.max_position_pct = max_position_pct
        self.min_avg_daily_volume = min_avg_daily_volume
        self.timeout_seconds = timeout_seconds
        self.model_name = resolve_model_name(model_name)
        self.tool_registry = tool_registry or TradingToolRegistry()
        self.agent = Agent(
            self.model_name,
            output_type=RiskDecision,
            instructions=(
                "You are the risk brain for a US equities paper-trading platform. "
                "Review the supplied account, signal, and market context and decide whether a trade should proceed. "
                "Be conservative. Reject trades when liquidity, buying power, or signal quality is inadequate. "
                "Return a share count only when the trade should be approved."
            ),
        )
    async def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        ticker = state.get("ticker", "UNKNOWN")
        signal = state.get("signal", "HOLD")
        market_data = state.get("market_data") or {}
        research_context = {
            "summary": state.get("research_summary"),
            "sentiment": state.get("research_sentiment"),
            "updates": state.get("research_updates") or [],
            "catalysts": state.get("research_catalysts") or [],
            "risk_flags": state.get("research_risk_flags") or [],
        }
        broker_connection = state.get("broker_connection")

        if not broker_connection:
            logger.info("[RiskAgent] No broker connection available for %s; rejecting execution path.", ticker)
            return self._missing_broker_response(ticker=ticker, signal=signal, market_data=market_data)

        account = await self._fetch_account(state, broker_connection)
        open_positions = await self._fetch_open_positions(state, broker_connection)
        portfolio_history = await self._fetch_portfolio_history(state, broker_connection)

        buying_power = float(account.get("buying_power") or 0.0)
        current_price = float(market_data.get("current_price") or 0.0)
        avg_daily_volume = float(market_data.get("avg_daily_volume") or 0.0)
        max_notional_allowed = buying_power * self.max_position_pct
        max_share_count = self._max_share_count(max_notional_allowed=max_notional_allowed, current_price=current_price)

        prompt = self._build_prompt(
            ticker=ticker,
            signal=signal,
            market_data=market_data,
            research_context=research_context,
            account=account,
            open_positions=open_positions,
            portfolio_history=portfolio_history,
            buying_power=buying_power,
            max_notional_allowed=max_notional_allowed,
            max_share_count=max_share_count,
        )
        result = await self.agent.run(prompt)
        llm_decision = result.output
        normalized = self._apply_hard_guardrails(
            ticker=ticker,
            signal=signal,
            market_data=market_data,
            llm_decision=llm_decision,
            buying_power=buying_power,
            current_price=current_price,
            avg_daily_volume=avg_daily_volume,
            max_notional_allowed=max_notional_allowed,
            max_share_count=max_share_count,
        )

        logger.info(
            "[RiskAgent] %s approved=%s shares=%s confidence=%.2f",
            ticker,
            normalized["risk_approved"],
            normalized["share_count"],
            normalized["risk_confidence"],
        )
        logger.info("[RiskAgent] %s", normalized["risk_reason"])
        return normalized

    async def _fetch_account(self, state: Dict[str, Any], broker_connection: Dict[str, Any]) -> Dict[str, Any]:
        context = ToolContext(state=state, agent_name="risk")
        try:
            return await self.tool_registry.call_tool(
                "get_account_balance",
                context=context,
                broker_connection=broker_connection,
            )
        except Exception as exc:
            logger.warning("[RiskAgent] Falling back to direct account fetch: %s", exc)
            return await self._fetch_account_direct(broker_connection)

    async def _fetch_account_direct(self, broker_connection: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.paper_base_url}/v2/account"
        headers = {
            "APCA-API-KEY-ID": broker_connection["api_key"],
            "APCA-API-SECRET-KEY": broker_connection["secret_key"],
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url, headers=headers)

        if response.status_code != 200:
            raise RiskError(
                f"Alpaca account request failed with status={response.status_code} body={response.text[:300]}"
            )

        payload = response.json()
        logger.info(
            "[RiskAgent] Alpaca account fetched. status=%s buying_power=%s equity=%s",
            payload.get("status"),
            payload.get("buying_power"),
            payload.get("equity"),
        )
        return payload

    async def _fetch_open_positions(self, state: Dict[str, Any], broker_connection: Dict[str, Any]) -> list[Dict[str, Any]]:
        context = ToolContext(state=state, agent_name="risk")
        try:
            return await self.tool_registry.call_tool(
                "get_open_positions",
                context=context,
                broker_connection=broker_connection,
            )
        except Exception as exc:
            logger.warning("[RiskAgent] get_open_positions failed: %s", exc)
            return []

    async def _fetch_portfolio_history(self, state: Dict[str, Any], broker_connection: Dict[str, Any]) -> Dict[str, Any]:
        context = ToolContext(state=state, agent_name="risk")
        try:
            return await self.tool_registry.call_tool(
                "get_portfolio_history",
                context=context,
                broker_connection=broker_connection,
            )
        except Exception as exc:
            logger.warning("[RiskAgent] get_portfolio_history failed: %s", exc)
            return {"status": "unavailable", "detail": str(exc)}

    def _missing_broker_response(self, ticker: str, signal: str, market_data: Dict[str, Any]) -> Dict[str, Any]:
        avg_daily_volume = float(market_data.get("avg_daily_volume") or 0.0)
        current_price = float(market_data.get("current_price") or 0.0)
        confidence = 1.0 if signal == "BUY" else 0.9
        reason = (
            f"Rejected {ticker}: no connected broker account is available. "
            "Sign in and connect an Alpaca paper account before execution is enabled."
        )
        return {
            "risk_approved": False,
            "share_count": 0,
            "risk_reason": reason,
            "risk_confidence": confidence,
            "risk_controls_triggered": ["missing_broker_connection"],
            "risk_details": {
                "approved": False,
                "reason": reason,
                "share_count": 0,
                "confidence": confidence,
                "max_notional_allowed": 0.0,
                "buying_power": 0.0,
                "current_price": current_price,
                "min_avg_daily_volume": self.min_avg_daily_volume,
                "avg_daily_volume": avg_daily_volume,
                "controls_triggered": ["missing_broker_connection"],
                "llm_recommended_share_count": 0,
            },
            "decision_model": self.model_name,
        }

    def _build_prompt(
        self,
        ticker: str,
        signal: str,
        market_data: Dict[str, Any],
        research_context: Dict[str, Any],
        account: Dict[str, Any],
        open_positions: list[Dict[str, Any]],
        portfolio_history: Dict[str, Any],
        buying_power: float,
        max_notional_allowed: float,
        max_share_count: int,
    ) -> str:
        return (
            "Evaluate whether this trade should pass the platform risk gate.\n\n"
            f"Ticker: {ticker}\n"
            f"Signal from strategy agent: {signal}\n"
            f"Max position policy: {self.max_position_pct:.2%}\n"
            f"Minimum average daily volume policy: {self.min_avg_daily_volume:,}\n"
            f"Derived max notional allowed: {max_notional_allowed:.2f}\n"
            f"Maximum shares allowed by policy: {max_share_count}\n\n"
            "Market data JSON:\n"
            f"{compact_json(market_data)}\n\n"
            "Research context JSON:\n"
            f"{compact_json(research_context)}\n\n"
            "Account JSON:\n"
            f"{compact_json(account)}\n\n"
            "Open positions JSON:\n"
            f"{compact_json({'positions': open_positions})}\n\n"
            "Portfolio history JSON:\n"
            f"{compact_json(portfolio_history)}\n\n"
            "Risk guidance:\n"
            "- Approve only when the signal is actionable and the trade fits liquidity and buying-power policy.\n"
            "- Reject non-BUY signals.\n"
            "- Never recommend more shares than the policy maximum.\n"
            "- Use current catalysts and risk flags from the research context when judging near-term execution risk.\n"
            "- Explain the most important controls that were triggered."
        )

    def _apply_hard_guardrails(
        self,
        ticker: str,
        signal: str,
        market_data: Dict[str, Any],
        llm_decision: RiskDecision,
        buying_power: float,
        current_price: float,
        avg_daily_volume: float,
        max_notional_allowed: float,
        max_share_count: int,
    ) -> Dict[str, Any]:
        controls_triggered = list(llm_decision.controls_triggered)

        approved = bool(llm_decision.approved)
        share_count = max(0, min(int(llm_decision.share_count), max_share_count))
        reason = llm_decision.reason

        if signal != "BUY":
            approved = False
            share_count = 0
            controls_triggered.append("non_buy_signal")
            reason = (
                f"Rejected {ticker}: strategy signal is {signal}. The execution workflow only permits BUY orders."
            )

        if avg_daily_volume < self.min_avg_daily_volume:
            approved = False
            share_count = 0
            controls_triggered.append("liquidity_below_threshold")
            reason = (
                f"Rejected {ticker}: average daily volume {avg_daily_volume:,.0f} is below the platform threshold "
                f"of {self.min_avg_daily_volume:,}."
            )

        if buying_power <= 0:
            approved = False
            share_count = 0
            controls_triggered.append("buying_power_not_positive")
            reason = f"Rejected {ticker}: account buying power is not positive ({buying_power:.2f})."

        if current_price <= 0:
            approved = False
            share_count = 0
            controls_triggered.append("invalid_price")
            reason = f"Rejected {ticker}: current price is invalid ({current_price:.2f})."

        if max_share_count < 1:
            approved = False
            share_count = 0
            controls_triggered.append("policy_allows_zero_shares")
            reason = (
                f"Rejected {ticker}: policy max notional {max_notional_allowed:.2f} at price {current_price:.2f} "
                "does not allow at least one share."
            )

        if approved and share_count < 1:
            approved = False
            share_count = 0
            controls_triggered.append("llm_zero_share_recommendation")
            reason = f"Rejected {ticker}: the risk agent did not produce a viable share count."

        risk_details = {
            "approved": approved,
            "reason": reason,
            "share_count": share_count,
            "confidence": llm_decision.confidence,
            "max_notional_allowed": max_notional_allowed,
            "buying_power": buying_power,
            "current_price": current_price,
            "min_avg_daily_volume": self.min_avg_daily_volume,
            "avg_daily_volume": avg_daily_volume,
            "controls_triggered": sorted(set(controls_triggered)),
            "llm_recommended_share_count": int(llm_decision.share_count),
        }
        return {
            "risk_approved": approved,
            "share_count": share_count,
            "risk_reason": reason,
            "risk_confidence": llm_decision.confidence,
            "risk_controls_triggered": risk_details["controls_triggered"],
            "risk_details": risk_details,
            "decision_model": self.model_name,
        }

    @staticmethod
    def _max_share_count(max_notional_allowed: float, current_price: float) -> int:
        if current_price <= 0:
            return 0
        return max(0, math.floor(max_notional_allowed / current_price))
