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
        position_context = self._position_context(ticker=ticker, open_positions=open_positions)
        shorting_enabled = bool(account.get("shorting_enabled", False))

        buying_power = float(account.get("buying_power") or 0.0)
        current_price = float(market_data.get("current_price") or 0.0)
        avg_daily_volume = float(market_data.get("avg_daily_volume") or 0.0)
        is_close_long = signal == "SELL" and position_context["long_quantity"] > 0
        is_cover_short = signal == "BUY" and position_context["short_quantity"] > 0
        is_exit = is_close_long or is_cover_short
        exit_quantity = position_context["long_quantity"] if is_close_long else position_context["short_quantity"] if is_cover_short else 0
        max_notional_allowed = position_context["position_market_value"] if is_exit else buying_power * self.max_position_pct
        max_share_count = exit_quantity if is_exit else self._max_share_count(max_notional_allowed=max_notional_allowed, current_price=current_price)

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
            position_context=position_context,
            shorting_enabled=shorting_enabled,
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
            position_context=position_context,
            shorting_enabled=shorting_enabled,
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
                "trade_action": "NO_TRADE",
                "execution_side": None,
                "position_context": {
                    "has_position": False,
                    "long_quantity": 0,
                    "short_quantity": 0,
                    "position_side": "flat",
                    "shorting_enabled": False,
                },
            },
            "decision_model": self.model_name,
            "trade_action": "NO_TRADE",
            "execution_side": None,
            "position_context": {
                "has_position": False,
                "long_quantity": 0,
                "short_quantity": 0,
                "position_side": "flat",
                "shorting_enabled": False,
            },
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
        position_context: Dict[str, Any],
        shorting_enabled: bool,
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
            "Position context JSON:\n"
            f"{compact_json(position_context)}\n\n"
            f"Account shorting enabled: {shorting_enabled}\n\n"
            "Open positions JSON:\n"
            f"{compact_json({'positions': open_positions})}\n\n"
            "Portfolio history JSON:\n"
            f"{compact_json(portfolio_history)}\n\n"
            "Risk guidance:\n"
            "- BUY with no same-ticker position opens a long position.\n"
            "- BUY with an existing short position should cover that short.\n"
            "- SELL with an existing long position should close that long.\n"
            "- SELL with no same-ticker position may open a short only if shorting is enabled.\n"
            "- Reject attempts to add to an existing long or short position.\n"
            "- HOLD signals should be rejected.\n"
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
        position_context: Dict[str, Any],
        shorting_enabled: bool,
    ) -> Dict[str, Any]:
        controls_triggered = list(llm_decision.controls_triggered)

        approved = bool(llm_decision.approved)
        share_count = max(0, min(int(llm_decision.share_count), max_share_count))
        reason = llm_decision.reason
        trade_action = "NO_TRADE"
        execution_side = None
        opens_new_position = False

        if signal == "HOLD":
            approved = False
            share_count = 0
            controls_triggered.append("hold_signal")
            reason = f"Rejected {ticker}: strategy signal is HOLD, so no trade should be placed."

        if signal == "BUY" and position_context["long_quantity"] > 0:
            approved = False
            share_count = 0
            controls_triggered.append("duplicate_long_position")
            reason = (
                f"Rejected {ticker}: an open long position already exists with quantity "
                f"{position_context['long_quantity']}."
            )

        if signal == "SELL" and position_context["short_quantity"] > 0:
            approved = False
            share_count = 0
            controls_triggered.append("duplicate_short_position")
            reason = (
                f"Rejected {ticker}: an open short position already exists with quantity "
                f"{position_context['short_quantity']}."
            )

        if signal == "SELL" and position_context["long_quantity"] > 0 and share_count < 1:
            share_count = position_context["long_quantity"]

        if signal == "BUY" and position_context["short_quantity"] > 0 and share_count < 1:
            share_count = position_context["short_quantity"]

        if signal == "SELL" and position_context["long_quantity"] < 1 and not shorting_enabled:
            approved = False
            share_count = 0
            controls_triggered.append("shorting_disabled")
            reason = f"Rejected {ticker}: short selling is not enabled on the connected account."

        if signal == "BUY" and position_context["short_quantity"] == 0 and position_context["long_quantity"] == 0:
            opens_new_position = True

        if signal == "SELL" and position_context["long_quantity"] == 0 and position_context["short_quantity"] == 0:
            opens_new_position = True

        if signal in {"BUY", "SELL"} and opens_new_position and avg_daily_volume < self.min_avg_daily_volume:
            approved = False
            share_count = 0
            controls_triggered.append("liquidity_below_threshold")
            reason = (
                f"Rejected {ticker}: average daily volume {avg_daily_volume:,.0f} is below the platform threshold "
                f"of {self.min_avg_daily_volume:,}."
            )

        if signal in {"BUY", "SELL"} and opens_new_position and buying_power <= 0:
            approved = False
            share_count = 0
            controls_triggered.append("buying_power_not_positive")
            reason = f"Rejected {ticker}: account buying power is not positive ({buying_power:.2f})."

        if signal == "SELL" and position_context["long_quantity"] < 1 and position_context["short_quantity"] < 1 and shorting_enabled and share_count < 1:
            approved = False
            share_count = 0
            controls_triggered.append("llm_zero_share_recommendation")
            reason = f"Rejected {ticker}: the risk agent did not produce a viable short share count."

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

        if approved and signal == "BUY" and position_context["short_quantity"] > 0:
            trade_action = "COVER_SHORT"
            execution_side = "buy"
        elif approved and signal == "BUY":
            trade_action = "OPEN_LONG"
            execution_side = "buy"
        elif approved and signal == "SELL" and position_context["long_quantity"] > 0:
            trade_action = "CLOSE_LONG"
            execution_side = "sell"
        elif approved and signal == "SELL":
            trade_action = "OPEN_SHORT"
            execution_side = "sell"

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
            "trade_action": trade_action,
            "execution_side": execution_side,
            "position_context": position_context,
        }
        return {
            "risk_approved": approved,
            "share_count": share_count,
            "risk_reason": reason,
            "risk_confidence": llm_decision.confidence,
            "risk_controls_triggered": risk_details["controls_triggered"],
            "risk_details": risk_details,
            "decision_model": self.model_name,
            "trade_action": trade_action,
            "execution_side": execution_side,
            "position_context": position_context,
        }

    @staticmethod
    def _max_share_count(max_notional_allowed: float, current_price: float) -> int:
        if current_price <= 0:
            return 0
        return max(0, math.floor(max_notional_allowed / current_price))

    @staticmethod
    def _position_context(ticker: str, open_positions: list[Dict[str, Any]]) -> Dict[str, Any]:
        normalized_ticker = str(ticker or "").strip().upper()
        for position in open_positions:
            symbol = str(position.get("symbol") or "").strip().upper()
            if symbol != normalized_ticker:
                continue
            quantity = abs(int(float(position.get("qty") or 0)))
            side = str(position.get("side") or "long").lower()
            market_value = abs(float(position.get("market_value") or 0.0))
            return {
                "has_position": quantity > 0,
                "position_side": side,
                "long_quantity": quantity if side == "long" else 0,
                "short_quantity": quantity if side == "short" else 0,
                "position_market_value": market_value,
            }
        return {
            "has_position": False,
            "position_side": "flat",
            "long_quantity": 0,
            "short_quantity": 0,
            "position_market_value": 0.0,
        }
