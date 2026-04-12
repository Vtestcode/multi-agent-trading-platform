from __future__ import annotations

import logging
from typing import Any

from agents.execution_agent import ExecutionAgent
from agents.market_data_agent import MarketDataAgent
from agents.risk_agent import RiskAgent
from agents.scanner_agent import MarketScannerAgent
from agents.strategy_agent import StrategyAgent
from agents.tool_registry import ToolContext, TradingToolRegistry
from agents.workflow_types import TradingState, ValidationReport

logger = logging.getLogger(__name__)


class CoordinatorAgent:
    """State-owning orchestrator that delegates work to specialist agents."""

    def __init__(
        self,
        tool_registry: TradingToolRegistry | None = None,
        market_data_agent: MarketDataAgent | None = None,
        scanner_agent: MarketScannerAgent | None = None,
        strategy_agent: StrategyAgent | None = None,
        risk_agent: RiskAgent | None = None,
        execution_agent: ExecutionAgent | None = None,
        max_validation_loops: int = 2,
    ) -> None:
        self.tool_registry = tool_registry or TradingToolRegistry()
        self.market_data_agent = market_data_agent or MarketDataAgent()
        self.scanner_agent = scanner_agent or MarketScannerAgent(market_data_agent=self.market_data_agent)
        self.strategy_agent = strategy_agent or StrategyAgent(tool_registry=self.tool_registry)
        self.risk_agent = risk_agent or RiskAgent(tool_registry=self.tool_registry)
        self.execution_agent = execution_agent or ExecutionAgent(tool_registry=self.tool_registry)
        self.max_validation_loops = max_validation_loops

    def initialize_state(self, initial_state: TradingState | None = None) -> TradingState:
        state: TradingState = dict(initial_state or {})
        state.setdefault("tool_inventory", self.tool_registry.catalog())
        state.setdefault("tool_history", [])
        state.setdefault("orchestration_trace", [])
        state.setdefault("validation_reports", [])
        state.setdefault("validation_status", "pending")
        return state

    async def run_scanner(self, state: TradingState) -> dict[str, Any]:
        state = self.initialize_state(state)
        manual_ticker = state.get("manual_ticker")
        excluded_tickers = state.get("excluded_tickers") or []
        self._trace(state, f"Coordinator delegated scanner step. manual_ticker={manual_ticker or 'AUTO'}")
        result = await self.scanner_agent.run(manual_ticker=manual_ticker, excluded_tickers=excluded_tickers)
        return {**result, **self._state_metadata(state)}

    async def run_market_data(self, state: TradingState) -> dict[str, Any]:
        ticker = state.get("selected_ticker") or state["ticker"]
        existing_market_data = state.get("market_data")
        if existing_market_data:
            self._trace(state, f"Coordinator reused scanner market data for {ticker}")
            return {"ticker": ticker, "market_data": existing_market_data, **self._state_metadata(state)}
        self._trace(state, f"Coordinator delegated market data step for {ticker}")
        result = await self.market_data_agent.run(ticker=ticker)
        return {**result, **self._state_metadata(state)}

    async def run_strategy(self, state: TradingState) -> dict[str, Any]:
        ticker = state["ticker"]
        self._trace(state, f"Coordinator delegated strategy step for {ticker}")
        result = await self.strategy_agent.run(state)
        return {**result, **self._state_metadata(state)}

    async def validate_strategy(self, state: TradingState) -> dict[str, Any]:
        return self._run_validation_loop(state, agent="strategy", validator=self._strategy_validator)

    async def run_risk(self, state: TradingState) -> dict[str, Any]:
        ticker = state["ticker"]
        self._trace(state, f"Coordinator delegated risk step for {ticker}")
        result = await self.risk_agent.run(state)
        return {**result, **self._state_metadata(state)}

    async def validate_risk(self, state: TradingState) -> dict[str, Any]:
        return self._run_validation_loop(state, agent="risk", validator=self._risk_validator)

    async def run_execution(self, state: TradingState) -> dict[str, Any]:
        ticker = state["ticker"]
        self._trace(state, f"Coordinator delegated execution step for {ticker}")
        result = await self.execution_agent.run(state)
        return {**result, **self._state_metadata(state)}

    async def validate_execution(self, state: TradingState) -> dict[str, Any]:
        return self._run_validation_loop(state, agent="execution", validator=self._execution_validator)

    def finalize_state(self, state: TradingState) -> dict[str, Any]:
        ticker = state.get("ticker") or state.get("selected_ticker") or "UNKNOWN"
        signal = state.get("signal") or "PENDING"
        execution = state.get("execution_status") or "PENDING"
        state["coordinator_summary"] = f"{ticker} -> {signal} -> {execution}"
        state["validation_status"] = "passed" if all(report.get("passed") for report in state["validation_reports"]) else "attention"
        self._trace(state, f"Coordinator finalized workflow with status {state['validation_status']}")
        return {
            "coordinator_summary": state["coordinator_summary"],
            "validation_status": state["validation_status"],
            **self._state_metadata(state),
        }

    def tool_context(self, state: TradingState, agent_name: str) -> ToolContext:
        return ToolContext(state=state, agent_name=agent_name)

    def _run_validation_loop(
        self,
        state: TradingState,
        agent: str,
        validator: Any,
    ) -> dict[str, Any]:
        last_report: ValidationReport | None = None
        for iteration in range(1, self.max_validation_loops + 1):
            report = validator(state, iteration)
            self._append_validation_report(state, report)
            last_report = report
            if report["passed"]:
                break
        if last_report is None:
            last_report = {
                "agent": agent,
                "passed": False,
                "iteration": 1,
                "summary": "Validator did not produce a report.",
                "issues": ["missing_validation_report"],
            }
            self._append_validation_report(state, last_report)
        self._trace(state, f"Validation loop completed for {agent}. passed={last_report['passed']}")
        return self._state_metadata(state)

    def _strategy_validator(self, state: TradingState, iteration: int) -> ValidationReport:
        signal = str(state.get("signal") or "")
        confidence = state.get("strategy_confidence")
        rationale = str(state.get("strategy_reason") or "")
        issues: list[str] = []
        if signal not in {"BUY", "SELL", "HOLD"}:
            issues.append("invalid_signal")
            state["signal"] = "HOLD"
        if not isinstance(confidence, (int, float)) or not (0.0 <= float(confidence) <= 1.0):
            issues.append("invalid_confidence")
            state["strategy_confidence"] = 0.0
        if len(rationale.strip()) < 12:
            issues.append("reason_too_short")
            state["strategy_reason"] = "Strategy output did not contain enough reasoning, so the coordinator downgraded it."
        return {
            "agent": "strategy",
            "passed": not issues,
            "iteration": iteration,
            "summary": "Strategy output validated." if not issues else "Strategy output required normalization.",
            "issues": issues,
        }

    def _risk_validator(self, state: TradingState, iteration: int) -> ValidationReport:
        approved = bool(state.get("risk_approved", False))
        share_count = int(state.get("share_count", 0) or 0)
        issues: list[str] = []
        if approved and share_count < 1:
            issues.append("approved_without_shares")
            state["risk_approved"] = False
            state["share_count"] = 0
        if not approved and share_count != 0:
            issues.append("rejected_with_nonzero_shares")
            state["share_count"] = 0
        if not state.get("risk_reason"):
            issues.append("missing_risk_reason")
            state["risk_reason"] = "Risk output was normalized by the coordinator because the original reason was missing."
        return {
            "agent": "risk",
            "passed": not issues,
            "iteration": iteration,
            "summary": "Risk output validated." if not issues else "Risk output required normalization.",
            "issues": issues,
        }

    def _execution_validator(self, state: TradingState, iteration: int) -> ValidationReport:
        execution_status = str(state.get("execution_status") or "")
        issues: list[str] = []
        if execution_status == "AWAITING_CONFIRMATION":
            return {
                "agent": "execution",
                "passed": True,
                "iteration": iteration,
                "summary": "Execution is waiting for explicit operator confirmation.",
                "issues": [],
            }
        if execution_status not in {"SUBMITTED", "SKIPPED", "FAILED"}:
            issues.append("invalid_execution_status")
            state["execution_status"] = "FAILED"
        if state.get("execution_status") == "SUBMITTED" and not state.get("order_response"):
            issues.append("submitted_without_order_response")
            state["execution_status"] = "FAILED"
            state["execution_detail"] = "Coordinator rejected an incomplete execution result."
        return {
            "agent": "execution",
            "passed": not issues,
            "iteration": iteration,
            "summary": "Execution output validated." if not issues else "Execution output required normalization.",
            "issues": issues,
        }

    def _append_validation_report(self, state: TradingState, report: ValidationReport) -> None:
        reports = list(state.get("validation_reports") or [])
        reports.append(report)
        state["validation_reports"] = reports

    @staticmethod
    def _state_metadata(state: TradingState) -> dict[str, Any]:
        return {
            "tool_inventory": state.get("tool_inventory") or {},
            "tool_history": list(state.get("tool_history") or []),
            "orchestration_trace": list(state.get("orchestration_trace") or []),
            "validation_reports": list(state.get("validation_reports") or []),
            "validation_status": state.get("validation_status") or "pending",
        }

    @staticmethod
    def _trace(state: TradingState, message: str) -> None:
        trace = list(state.get("orchestration_trace") or [])
        trace.append(message)
        state["orchestration_trace"] = trace
        logger.info("[CoordinatorAgent] %s", message)
