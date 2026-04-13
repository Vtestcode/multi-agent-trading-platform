from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langsmith import traceable

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agents.coordinator_agent import CoordinatorAgent
from agents.workflow_types import TradingState
from observability import configure_observability, workflow_tracing_context

load_dotenv()
configure_observability()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("trading_system")


def create_coordinator() -> CoordinatorAgent:
    return CoordinatorAgent()


def build_graph():
    coordinator = create_coordinator()

    async def scanner_node(state: TradingState) -> Dict[str, Any]:
        return await coordinator.run_scanner(state)

    async def market_data_node(state: TradingState) -> Dict[str, Any]:
        return await coordinator.run_market_data(state)

    async def strategy_node(state: TradingState) -> Dict[str, Any]:
        return await coordinator.run_strategy(state)

    async def research_node(state: TradingState) -> Dict[str, Any]:
        return await coordinator.run_research(state)

    async def validate_strategy_node(state: TradingState) -> Dict[str, Any]:
        return await coordinator.validate_strategy(state)

    async def risk_node(state: TradingState) -> Dict[str, Any]:
        return await coordinator.run_risk(state)

    async def validate_risk_node(state: TradingState) -> Dict[str, Any]:
        return await coordinator.validate_risk(state)

    async def execution_node(state: TradingState) -> Dict[str, Any]:
        return await coordinator.run_execution(state)

    async def validate_execution_node(state: TradingState) -> Dict[str, Any]:
        return await coordinator.validate_execution(state)

    async def finalize_node(state: TradingState) -> Dict[str, Any]:
        return coordinator.finalize_state(state)

    graph = StateGraph(TradingState)
    graph.add_node("scan_market", scanner_node)
    graph.add_node("fetch_market_data", market_data_node)
    graph.add_node("research_market", research_node)
    graph.add_node("generate_signal", strategy_node)
    graph.add_node("validate_signal", validate_strategy_node)
    graph.add_node("risk_check", risk_node)
    graph.add_node("validate_risk", validate_risk_node)
    graph.add_node("execute_trade", execution_node)
    graph.add_node("validate_execution", validate_execution_node)
    graph.add_node("finalize_run", finalize_node)

    graph.set_entry_point("scan_market")
    graph.add_edge("scan_market", "fetch_market_data")
    graph.add_edge("fetch_market_data", "research_market")
    graph.add_edge("research_market", "generate_signal")
    graph.add_edge("generate_signal", "validate_signal")
    graph.add_edge("validate_signal", "risk_check")
    graph.add_edge("risk_check", "validate_risk")
    graph.add_conditional_edges(
        "validate_risk",
        route_after_risk,
        {
            "execute_trade": "execute_trade",
            "finalize_run": "finalize_run",
        },
    )
    graph.add_edge("execute_trade", "validate_execution")
    graph.add_edge("validate_execution", "finalize_run")
    graph.add_edge("finalize_run", END)
    return graph.compile()


def route_after_risk(state: TradingState) -> str:
    approved = bool(state.get("risk_approved", False))
    share_count = int(state.get("share_count", 0) or 0)
    logger.info("[Orchestrator] Risk routing decision risk_approved=%s share_count=%s", approved, share_count)
    return "execute_trade" if approved and share_count > 0 else "finalize_run"


@traceable(name="run_trading_loop")
async def run_trading_loop(
    ticker: str | None = None,
    broker_connection: Dict[str, Any] | None = None,
    broker_connection_summary: Dict[str, Any] | None = None,
    excluded_tickers: list[str] | None = None,
    allow_execution: bool = False,
) -> TradingState:
    app = build_graph()
    normalized_ticker = ticker.strip().upper() if ticker else None
    initial_state: TradingState = {
        "manual_ticker": normalized_ticker,
        "broker_connection": broker_connection,
        "broker_connection_summary": broker_connection_summary,
        "allow_execution": allow_execution,
        "excluded_tickers": excluded_tickers or [],
    }
    logger.info("[Orchestrator] Running trading loop manual_ticker=%s", normalized_ticker)
    with workflow_tracing_context(normalized_ticker or "AUTO"):
        final_state = await app.ainvoke(initial_state)
    logger.info("[Orchestrator] Final state: %s", final_state)
    return final_state


def run_trading_loop_sync(
    ticker: str | None = None,
    broker_connection: Dict[str, Any] | None = None,
    broker_connection_summary: Dict[str, Any] | None = None,
    excluded_tickers: list[str] | None = None,
    allow_execution: bool = False,
) -> TradingState:
    return asyncio.run(run_trading_loop(ticker, broker_connection, broker_connection_summary, excluded_tickers, allow_execution))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM-driven trading platform with tool-driven multi-agent orchestration")
    parser.add_argument("ticker", nargs="?", type=str, help="Optional manual ticker override, for example AAPL")
    parser.add_argument("--pretty", action="store_true", help="Print final state as pretty JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    final_state = asyncio.run(run_trading_loop(args.ticker))
    if args.pretty:
        print(json.dumps(final_state, indent=2, sort_keys=True, default=str))
    else:
        print(json.dumps(final_state, default=str))


if __name__ == "__main__":
    main()
