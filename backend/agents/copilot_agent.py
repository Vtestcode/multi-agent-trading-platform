from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from sqlalchemy.orm import Session

from agents.coordinator_agent import CoordinatorAgent
from agents.llm_common import compact_json, resolve_model_name
from agents.tool_registry import ToolRegistryError, TradingToolRegistry
from history_store import has_pending_execution_approval, list_workflow_run_states, summarize_workflow_runs
from integrations import get_broker_connection, resolve_execution_credentials
from models import User

logger = logging.getLogger(__name__)


class QueryInterpretation(BaseModel):
    normalized_query: str = Field(min_length=4)
    user_intent: Literal[
        "general_question",
        "workflow_request",
        "market_scan",
        "tool_request",
        "trade_execution",
        "history_analysis",
    ]
    requested_ticker: str | None = Field(default=None, max_length=20)
    retrieval_focus: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    notes: str = Field(min_length=8)


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


class CopilotDeliberation(BaseModel):
    understanding: str = Field(min_length=12)
    evidence_used: list[str] = Field(default_factory=list)
    risks_or_gaps: list[str] = Field(default_factory=list)
    decision_summary: str = Field(min_length=12)


EventEmitter = Callable[[dict[str, Any]], Awaitable[None] | None]


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
        self.query_translator = Agent(
            self.model_name,
            output_type=QueryInterpretation,
            instructions=(
                "You are a query translation layer for a trading platform copilot. "
                "Rewrite the operator's request into a normalized query optimized for retrieval and intent classification. "
                "Identify the main user intent, requested ticker if any, useful retrieval focus areas, and material constraints. "
                "Be literal and precise. Do not invent missing facts."
            ),
        )
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
        self.deliberator = Agent(
            self.model_name,
            output_type=CopilotDeliberation,
            instructions=(
                "You are the internal deliberation layer for a trading platform copilot. "
                "Given the translated query, planned action, workflow context, history, and action result, produce a concise structured reasoning summary. "
                "Focus on what the user is really asking, which evidence matters most, and any remaining uncertainty. "
                "Keep the reasoning compact, grounded, and action-oriented."
            ),
        )
        self.responder = Agent(
            self.model_name,
            output_type=CopilotAnswer,
            instructions=(
                "You are the AI copilot for a multi-agent equity trading platform. "
                "Use the provided translated query, structured deliberation, tool results, coordinator state, and run history to answer the operator. "
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
        return await self._run(question, workflow_state, db=db, current_user=current_user)

    async def stream_answer(
        self,
        question: str,
        workflow_state: dict[str, Any] | None,
        db: Session | None = None,
        current_user: User | None = None,
        emit: EventEmitter | None = None,
    ) -> dict[str, Any]:
        return await self._run(question, workflow_state, db=db, current_user=current_user, emit=emit)

    async def _run(
        self,
        question: str,
        workflow_state: dict[str, Any] | None,
        db: Session | None = None,
        current_user: User | None = None,
        emit: EventEmitter | None = None,
    ) -> dict[str, Any]:
        history = list_workflow_run_states(db, current_user, limit=8) if db and current_user else []
        interpretation = await self._translate_query(question, workflow_state, history, emit=emit)
        await self._emit(
            emit,
            {
                "type": "status",
                "stage": "context",
                "message": self._context_message(workflow_state=workflow_state, history=history, interpretation=interpretation),
            },
        )
        planner_prompt = self._build_planner_prompt(
            question=question,
            interpretation=interpretation,
            workflow_state=workflow_state,
            history=history,
        )
        plan = (await self.planner.run(planner_prompt)).output
        plan = self._apply_plan_overrides(plan, question, interpretation)
        await self._emit(
            emit,
            {
                "type": "status",
                "stage": "planning",
                "message": f"Selected next step: {plan.action}. {plan.rationale}",
            },
        )
        action_result = await self._execute_plan(plan, question, interpretation, workflow_state, history, db, current_user, emit=emit)
        deliberation = await self._deliberate(
            question=question,
            interpretation=interpretation,
            workflow_state=workflow_state,
            history=history,
            plan=plan,
            action_result=action_result,
            emit=emit,
        )
        reply = await self._compose_answer(
            question,
            interpretation,
            deliberation,
            workflow_state,
            history,
            plan,
            action_result,
            emit=emit,
        )
        for chunk in self._reply_chunks(reply.reply):
            await self._emit(
                emit,
                {
                    "type": "reply_chunk",
                    "stage": "final",
                    "content": chunk,
                },
            )
        await self._emit(
            emit,
            {
                "type": "result",
                "stage": "final",
                "message": "Copilot response ready.",
                "reply": reply.reply,
                "action_taken": reply.action_taken,
                "action_result": reply.action_result,
                "model": self.model_name,
            },
        )
        return {
            "reply": reply.reply,
            "action_taken": reply.action_taken,
            "action_result": reply.action_result,
        }

    def _build_planner_prompt(
        self,
        question: str,
        interpretation: QueryInterpretation,
        workflow_state: dict[str, Any] | None,
        history: list[dict[str, Any]],
    ) -> str:
        return (
            "Plan the next copilot action.\n\n"
            f"Operator question:\n{question.strip()}\n\n"
            f"Translated query:\n{interpretation.model_dump_json(indent=2)}\n\n"
            f"Latest workflow state:\n{compact_json(workflow_state or {'status': 'missing'})}\n\n"
            f"Run history summary:\n{compact_json({'runs': summarize_workflow_runs(history)})}\n\n"
            f"Available tools:\n{compact_json(self.tool_registry.catalog())}\n"
        )

    async def _execute_plan(
        self,
        plan: CopilotPlan,
        question: str,
        interpretation: QueryInterpretation,
        workflow_state: dict[str, Any] | None,
        history: list[dict[str, Any]],
        db: Session | None,
        current_user: User | None,
        emit: EventEmitter | None = None,
    ) -> Any:
        logger.info("[CopilotAgent] action=%s rationale=%s", plan.action, plan.rationale)
        if plan.action == "answer":
            await self._emit(
                emit,
                {
                    "type": "status",
                    "stage": "action",
                    "message": "No platform action needed. Drafting a direct answer.",
                },
            )
            return None
        if plan.action == "history_lookup":
            await self._emit(
                emit,
                {
                    "type": "status",
                    "stage": "action",
                    "message": f"Checking recent saved runs across {len(history)} history items.",
                },
            )
            return {
                "history_summary": summarize_workflow_runs(history),
                "history_count": len(history),
            }
        if plan.action == "scan_market":
            await self._emit(
                emit,
                {
                    "type": "status",
                    "stage": "action",
                    "message": "Launching a fresh market scan from the coordinator.",
                },
            )
            state = self.coordinator.initialize_state(
                {
                    "manual_ticker": None,
                    "excluded_tickers": [],
                }
            )
            return await self.coordinator.run_scanner(state)
        if plan.action == "run_workflow":
            ticker = (plan.ticker or interpretation.requested_ticker or self._extract_ticker(question) or "").strip().upper() or None
            await self._emit(
                emit,
                {
                    "type": "status",
                    "stage": "action",
                    "message": f"Running the trading workflow for {ticker or 'an auto-selected candidate'}.",
                },
            )
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
            inferred_ticker = plan.ticker or interpretation.requested_ticker or self._extract_ticker(question)
            if inferred_ticker and "ticker" not in params and "symbol" not in params and "underlying_symbol" not in params:
                params["ticker"] = inferred_ticker
            if plan.requires_broker:
                broker_connection = self._resolve_broker(db, current_user)
                if broker_connection is None:
                    return {"error": "A connected Alpaca account is required for this action."}
                params.setdefault("broker_connection", broker_connection)
            await self._emit(
                emit,
                {
                    "type": "status",
                    "stage": "action",
                    "message": f"Calling tool {tool_name} with the most relevant inferred parameters.",
                },
            )
            try:
                return await self.tool_registry.call_tool(tool_name, **params)
            except ToolRegistryError as exc:
                return {"error": str(exc), "tool_name": tool_name}
        if plan.action == "execute_trade":
            broker_connection = self._resolve_broker(db, current_user)
            if broker_connection is None:
                return {"error": "A connected Alpaca account is required to execute trades."}
            ticker = (plan.ticker or interpretation.requested_ticker or self._extract_ticker(question) or "").strip().upper()
            if not ticker:
                return {"error": "No ticker was provided for trade execution."}
            allow_execution = self._is_execution_confirmation_request(question)
            if allow_execution and not has_pending_execution_approval(db, current_user, ticker):
                return {
                    "error": (
                        f"There is no pending execution approval for {ticker}. "
                        "Run the trade analysis first, then approve the pending execution."
                    )
                }
            await self._emit(
                emit,
                {
                    "type": "status",
                    "stage": "action",
                    "message": (
                        f"Submitting the approved trade for {ticker}."
                        if allow_execution
                        else f"Running the execution-ready workflow for {ticker} and checking whether submission can proceed."
                    ),
                },
            )
            final_state = await self._run_workflow(
                ticker=ticker,
                broker_connection=broker_connection,
                allow_execution=allow_execution,
            )
            return final_state
        return {"error": f"Unsupported action: {plan.action}"}

    async def _translate_query(
        self,
        question: str,
        workflow_state: dict[str, Any] | None,
        history: list[dict[str, Any]],
        emit: EventEmitter | None = None,
    ) -> QueryInterpretation:
        await self._emit(
            emit,
            {
                "type": "status",
                "stage": "translation",
                "message": "Translating your request into a cleaner search-and-intent query.",
            },
        )
        prompt = (
            "Translate the operator request for retrieval and intent classification.\n\n"
            f"Operator question:\n{question.strip()}\n\n"
            f"Latest workflow state:\n{compact_json(workflow_state or {'status': 'missing'})}\n\n"
            f"Run history summary:\n{compact_json({'runs': summarize_workflow_runs(history)})}\n"
        )
        interpretation = (await self.query_translator.run(prompt)).output
        await self._emit(
            emit,
            {
                "type": "status",
                "stage": "translation",
                "message": (
                    f"Interpreted intent as {interpretation.user_intent.replace('_', ' ')}"
                    + (f" with ticker focus {interpretation.requested_ticker}." if interpretation.requested_ticker else ".")
                ),
            },
        )
        return interpretation

    async def _deliberate(
        self,
        question: str,
        interpretation: QueryInterpretation,
        workflow_state: dict[str, Any] | None,
        history: list[dict[str, Any]],
        plan: CopilotPlan,
        action_result: dict[str, Any] | None,
        emit: EventEmitter | None = None,
    ) -> CopilotDeliberation:
        await self._emit(
            emit,
            {
                "type": "status",
                "stage": "reasoning",
                "message": "Reviewing the evidence and forming a structured reasoning summary before replying.",
            },
        )
        prompt = (
            "Produce a compact structured reasoning summary for the copilot response.\n\n"
            f"Operator question:\n{question.strip()}\n\n"
            f"Translated query:\n{interpretation.model_dump_json(indent=2)}\n\n"
            f"Plan:\n{plan.model_dump_json(indent=2)}\n\n"
            f"Latest workflow state:\n{compact_json(workflow_state or {'status': 'missing'})}\n\n"
            f"Run history:\n{compact_json({'runs': history})}\n\n"
            f"Action result:\n{compact_json(action_result or {'status': 'none'})}\n"
        )
        deliberation = (await self.deliberator.run(prompt)).output
        await self._emit(
            emit,
            {
                "type": "status",
                "stage": "reasoning",
                "message": deliberation.decision_summary,
            },
        )
        return deliberation

    async def _compose_answer(
        self,
        question: str,
        interpretation: QueryInterpretation,
        deliberation: CopilotDeliberation,
        workflow_state: dict[str, Any] | None,
        history: list[dict[str, Any]],
        plan: CopilotPlan,
        action_result: dict[str, Any] | None,
        emit: EventEmitter | None = None,
    ) -> CopilotAnswer:
        await self._emit(
            emit,
            {
                "type": "status",
                "stage": "response",
                "message": "Summarizing the result into an operator-facing reply.",
            },
        )
        prompt = (
            "Answer the operator's request using the available platform context.\n\n"
            f"Operator question:\n{question.strip()}\n\n"
            f"Translated query:\n{interpretation.model_dump_json(indent=2)}\n\n"
            f"Structured reasoning:\n{deliberation.model_dump_json(indent=2)}\n\n"
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
            if self._is_workspace_result(action_result):
                answer.action_result = action_result
            else:
                answer.action_result = self._preview_any(action_result)
        if not answer.action_taken:
            answer.action_taken = plan.action
        return answer

    @staticmethod
    def _context_message(
        workflow_state: dict[str, Any] | None,
        history: list[dict[str, Any]],
        interpretation: QueryInterpretation,
    ) -> str:
        context_bits: list[str] = []
        if workflow_state:
            context_bits.append("latest workspace state")
        if history:
            context_bits.append("recent run history")
        if interpretation.retrieval_focus:
            context_bits.append(", ".join(interpretation.retrieval_focus[:2]))
        if not context_bits:
            return "Reviewing your request with the currently available workspace context."
        return "Reviewing your request against " + ", ".join(context_bits) + "."

    def _apply_plan_overrides(
        self,
        plan: CopilotPlan,
        question: str,
        interpretation: QueryInterpretation,
    ) -> CopilotPlan:
        text = f"{question} {interpretation.normalized_query}".lower()
        ticker = (plan.ticker or interpretation.requested_ticker or self._extract_ticker(question) or "").strip().upper() or None

        if self._is_scan_request(text):
            plan.action = "scan_market"
            plan.ticker = None
            plan.tool_name = None
            plan.parameters = {}
            plan.requires_broker = False
            if "scan" not in plan.rationale.lower():
                plan.rationale = "The operator explicitly asked to run a new market scan."
            return plan

        if self._is_execution_request(text, ticker):
            plan.action = "execute_trade"
            plan.ticker = ticker
            plan.tool_name = None
            plan.parameters = {}
            plan.requires_broker = True
            if self._is_execution_confirmation_request(text):
                plan.rationale = "The operator explicitly asked to approve or confirm the pending trade."
            elif "execute" not in plan.rationale.lower() and "trade" not in plan.rationale.lower():
                plan.rationale = "The operator explicitly asked to place or execute a trade."
            return plan

        if self._is_workflow_request(text, ticker):
            plan.action = "run_workflow"
            plan.ticker = ticker
            plan.tool_name = None
            plan.parameters = {}
            plan.requires_broker = False
            if "workflow" not in plan.rationale.lower() and "run" not in plan.rationale.lower():
                plan.rationale = "The operator explicitly asked to run platform analysis for a ticker."
            return plan

        return plan

    @staticmethod
    def _is_scan_request(text: str) -> bool:
        scan_phrases = (
            "run new scan",
            "run a new scan",
            "start new scan",
            "scan the market",
            "run scan",
            "start scan",
            "new scan",
            "find candidates",
            "scan for setups",
        )
        return any(phrase in text for phrase in scan_phrases)

    @staticmethod
    def _is_workflow_request(text: str, ticker: str | None) -> bool:
        if not ticker:
            return False
        workflow_phrases = (
            "run ",
            "analyze ",
            "analyse ",
            "check ",
            "review ",
        )
        return any(phrase in text for phrase in workflow_phrases)

    @staticmethod
    def _is_execution_request(text: str, ticker: str | None) -> bool:
        if not ticker:
            return False
        execution_phrases = (
            "buy ",
            "place order",
            "place the order",
            "execute ",
            "submit ",
            "approve ",
            "confirm ",
            "proceed ",
        )
        return any(phrase in text for phrase in execution_phrases)

    @staticmethod
    def _is_execution_confirmation_request(text: str) -> bool:
        confirmation_phrases = (
            "approve ",
            "confirm ",
            "proceed ",
            "submit the order",
            "place the order",
        )
        return any(phrase in text for phrase in confirmation_phrases)

    @staticmethod
    async def _emit(emit: EventEmitter | None, event: dict[str, Any]) -> None:
        if emit is None:
            return
        maybe_awaitable = emit(event)
        if maybe_awaitable is not None:
            await maybe_awaitable

    @staticmethod
    def _reply_chunks(reply: str) -> list[str]:
        parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", reply.strip()) if part.strip()]
        return parts or ([reply.strip()] if reply.strip() else [])

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
        research_result = await self.coordinator.run_research(state)
        state.update(research_result)
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

    @staticmethod
    def _is_workspace_result(value: Any) -> bool:
        return isinstance(value, dict) and bool(value.get("ticker")) and (
            value.get("signal") is not None
            or value.get("scanner_summary") is not None
            or value.get("market_data") is not None
        )
