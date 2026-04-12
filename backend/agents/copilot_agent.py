from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from sqlalchemy.orm import Session

from agents.coordinator_agent import CoordinatorAgent
from agents.llm_common import compact_json, resolve_model_name
from agents.tool_registry import ToolRegistryError, TradingToolRegistry
from history_store import list_workflow_run_states, summarize_workflow_runs
from integrations import get_broker_connection, resolve_execution_credentials
from models import User

logger = logging.getLogger(__name__)


class CopilotPlan(BaseModel):
    action: Literal["answer", "run_workflow", "scan_market", "call_tool", "execute_trade", "history_lookup"] = "answer"
    rationale: str = Field(min_length=8)
    ticker: str | None = Field(default=None, max_length=20)
    tool_name: str | None = Field(default=None, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict)
    use_history: bool = True
    requires_broker: bool = False


class CopilotAnswer(BaseModel):
    reply: str
    action_taken: str
    action_result: dict[str, Any] | list[Any] | str | None = None


class CopilotAgent:
    """Operator copilot with workflow awareness, tool use, and action execution."""

    def __init__(
        self,
        model_name: str | None = None,
        tool_registry: TradingToolRegistry | None = None,
        coordinator: CoordinatorAgent | None = None,
    ) -> None:
        self.model_name = resolve_model_name(model_name)
        self.tool_registry = tool_registry or TradingToolRegistry()
        self.coordinator = coordinator or CoordinatorAgent(tool_registry=self.tool_registry)
        self.planner = Agent(
            self.model_name,
            output_type=CopilotPlan,
            instructions=(
                "You are the planning layer for an operator copilot in a multi-agent trading platform. "
                "Choose the single best next action for the user's request. "
                "Use answer for pure explanation, history_lookup for cross-run analysis, run_workflow when the user asks to run or analyze a ticker, "
                "scan_market when the user asks to scan or find candidates, call_tool when a specific tool is the clearest fit, "
                "and execute_trade only when the user explicitly asks to place or execute a trade. "
                "Only set requires_broker=true when the action truly needs a connected broker."
            ),
        )
        self.responder = Agent(
            self.model_name,
            output_type=CopilotAnswer,
            instructions=(
                "You are the AI copilot for a multi-agent equity trading platform. "
                "Use the provided context, tool results, coordinator state, and run history to answer the operator. "
                "Be concise, practical, and honest about uncertainty. "
                "Do not say a trade was executed unless the action result or workflow state clearly shows execution_status=SUBMITTED."
            ),
        )

    async def answer(
        self,
        question: str,
        workflow_state: dict[str, Any] | None,
        db: Session | None = None,
        current_user: User | None = None,
    ) -> dict[str, Any]:
        history = list_workflow_run_states(db, current_user, limit=8) if db and current_user else []
        planner_prompt = self._build_planner_prompt(question=question, workflow_state=workflow_state, history=history)
        plan = (await self.planner.run(planner_prompt)).output
        action_result = await self._execute_plan(plan, question, workflow_state, history, db, current_user)
        reply = await self._compose_answer(question, workflow_state, history, plan, action_result)
        return {
            "reply": reply.reply,
            "action_taken": reply.action_taken,
            "action_result": reply.action_result,
        }

    def _build_planner_prompt(
        self,
        question: str,
        workflow_state: dict[str, Any] | None,
        history: list[dict[str, Any]],
    ) -> str:
        return (
            "Plan the next copilot action.\n\n"
            f"Operator question:\n{question.strip()}\n\n"
            f"Latest workflow state:\n{compact_json(workflow_state or {'status': 'missing'})}\n\n"
            f"Run history summary:\n{compact_json({'runs': summarize_workflow_runs(history)})}\n\n"
            f"Available tools:\n{compact_json(self.tool_registry.catalog())}\n"
        )

    async def _execute_plan(
        self,
        plan: CopilotPlan,
        question: str,
        workflow_state: dict[str, Any] | None,
        history: list[dict[str, Any]],
        db: Session | None,
        current_user: User | None,
    ) -> Any:
        logger.info("[CopilotAgent] action=%s rationale=%s", plan.action, plan.rationale)
        if plan.action == "answer":
            return None
        if plan.action == "history_lookup":
            return {
                "history_summary": summarize_workflow_runs(history),
                "history_count": len(history),
            }
        if plan.action == "scan_market":
            state = self.coordinator.initialize_state(
                {
                    "manual_ticker": None,
                    "excluded_tickers": [],
                }
            )
            return await self.coordinator.run_scanner(state)
        if plan.action == "run_workflow":
            ticker = (plan.ticker or self._extract_ticker(question) or "").strip().upper() or None
            broker_connection = self._resolve_broker(db, current_user) if plan.requires_broker else None
            final_state = await self._run_workflow(
                ticker=ticker,
                broker_connection=broker_connection,
                allow_execution=False,
            )
            return final_state
        if plan.action == "call_tool":
            tool_name = (plan.tool_name or "").strip()
            if not tool_name:
                return {"error": "Planner did not provide a tool name."}
            params = dict(plan.parameters)
            inferred_ticker = plan.ticker or self._extract_ticker(question)
            if inferred_ticker and "ticker" not in params and "symbol" not in params and "underlying_symbol" not in params:
                params["ticker"] = inferred_ticker
            if plan.requires_broker:
                broker_connection = self._resolve_broker(db, current_user)
                if broker_connection is None:
                    return {"error": "A connected Alpaca account is required for this action."}
                params.setdefault("broker_connection", broker_connection)
            try:
                return await self.tool_registry.call_tool(tool_name, **params)
            except ToolRegistryError as exc:
                return {"error": str(exc), "tool_name": tool_name}
        if plan.action == "execute_trade":
            broker_connection = self._resolve_broker(db, current_user)
            if broker_connection is None:
                return {"error": "A connected Alpaca account is required to execute trades."}
            ticker = (plan.ticker or self._extract_ticker(question) or "").strip().upper()
            if not ticker:
                return {"error": "No ticker was provided for trade execution."}
            final_state = await self._run_workflow(
                ticker=ticker,
                broker_connection=broker_connection,
                allow_execution=True,
            )
            return final_state
        return {"error": f"Unsupported action: {plan.action}"}

    async def _compose_answer(
        self,
        question: str,
        workflow_state: dict[str, Any] | None,
        history: list[dict[str, Any]],
        plan: CopilotPlan,
        action_result: dict[str, Any] | None,
    ) -> CopilotAnswer:
        prompt = (
            "Answer the operator's request using the available platform context.\n\n"
            f"Operator question:\n{question.strip()}\n\n"
            f"Plan:\n{plan.model_dump_json(indent=2)}\n\n"
            f"Latest workflow state:\n{compact_json(workflow_state or {'status': 'missing'})}\n\n"
            f"Run history:\n{compact_json({'runs': history})}\n\n"
            f"Action result:\n{compact_json(action_result or {'status': 'none'})}\n\n"
            "Response guidance:\n"
            "- If you executed an action, summarize what happened first.\n"
            "- If history is relevant, mention trends across runs.\n"
            "- If the user asked for execution and execution did not happen, explain exactly why.\n"
            "- Suggest a next step when useful.\n"
            "- Set action_taken to the plan action.\n"
            "- Include a compact action_result object when one exists.\n"
        )
        result = await self.responder.run(prompt)
        answer = result.output
        if action_result is not None:
            answer.action_result = self._preview_any(action_result)
        if not answer.action_taken:
            answer.action_taken = plan.action
        return answer

    async def _run_workflow(
        self,
        ticker: str | None,
        broker_connection: dict[str, Any] | None,
        allow_execution: bool = False,
    ) -> dict[str, Any]:
        state = self.coordinator.initialize_state(
            {
                "manual_ticker": ticker,
                "excluded_tickers": [],
                "broker_connection": broker_connection,
                "allow_execution": allow_execution,
            }
        )
        scanner_result = await self.coordinator.run_scanner(state)
        state.update(scanner_result)
        market_result = await self.coordinator.run_market_data(state)
        state.update(market_result)
        strategy_result = await self.coordinator.run_strategy(state)
        state.update(strategy_result)
        state.update(await self.coordinator.validate_strategy(state))
        risk_result = await self.coordinator.run_risk(state)
        state.update(risk_result)
        state.update(await self.coordinator.validate_risk(state))
        if state.get("risk_approved") and int(state.get("share_count", 0) or 0) > 0:
            execution_result = await self.coordinator.run_execution(state)
            state.update(execution_result)
            state.update(await self.coordinator.validate_execution(state))
        state.update(self.coordinator.finalize_state(state))
        return state

    @staticmethod
    def _resolve_broker(db: Session | None, current_user: User | None) -> dict[str, Any] | None:
        if db is None or current_user is None:
            return None
        connection = get_broker_connection(db, current_user.id, "alpaca")
        if connection is None:
            return None
        try:
            return resolve_execution_credentials(connection)
        except Exception as exc:
            logger.warning("[CopilotAgent] Broker credentials could not be resolved: %s", exc)
            return None

    @staticmethod
    def _extract_ticker(question: str) -> str | None:
        match = re.search(r"\b[A-Z]{1,5}\b", question.upper())
        return match.group(0) if match else None

    @staticmethod
    def _preview_any(value: Any) -> dict[str, Any] | list[Any] | str:
        if isinstance(value, dict):
            return {key: value[key] for key in list(value)[:12]}
        if isinstance(value, list):
            return value[:6]
        return str(value)
