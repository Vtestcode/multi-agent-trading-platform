from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any, Dict, Literal, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph

from agents.execution_agent import ExecutionAgent
from agents.market_data_agent import MarketDataAgent
from agents.risk_agent import RiskAgent
from agents.strategy_agent import StrategyAgent

load_dotenv()


class TradingState(TypedDict, total=False):
    ticker: str
    market_data: Dict[str, Any]
    signal: Literal["BUY", "SELL", "HOLD"]
    strategy_reason: str
    strategy_confidence: float
    strategy_risks: list[str]
    risk_approved: bool
    risk_reason: str
    risk_confidence: float
    risk_controls_triggered: list[str]
    risk_details: Dict[str, Any]
    share_count: int
    execution_status: str
    execution_detail: str
    execution_tool: str | None
    order_response: Dict[str, Any] | None
    decision_model: str | None


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("trading_system")


def create_agents() -> tuple[MarketDataAgent, StrategyAgent, RiskAgent, ExecutionAgent]:
    return (
        MarketDataAgent(),
        StrategyAgent(),
        RiskAgent(),
        ExecutionAgent(),
    )


def build_nodes(
    market_data_agent: MarketDataAgent,
    strategy_agent: StrategyAgent,
    risk_agent: RiskAgent,
    execution_agent: ExecutionAgent,
):
    async def market_data_node(state: TradingState) -> Dict[str, Any]:
        ticker = state["ticker"]
        logger.info("[Orchestrator] Starting market data node for %s", ticker)
        return await market_data_agent.run(ticker=ticker)

    async def strategy_node(state: TradingState) -> Dict[str, Any]:
        ticker = state["ticker"]
        logger.info("[Orchestrator] Starting strategy node for %s", ticker)
        return await strategy_agent.run(state)

    async def risk_node(state: TradingState) -> Dict[str, Any]:
        ticker = state["ticker"]
        logger.info("[Orchestrator] Starting risk node for %s", ticker)
        return await risk_agent.run(state)

    async def execution_node(state: TradingState) -> Dict[str, Any]:
        ticker = state["ticker"]
        logger.info("[Orchestrator] Starting execution node for %s", ticker)
        return await execution_agent.run(state)

    return market_data_node, strategy_node, risk_node, execution_node


def route_after_risk(state: TradingState) -> str:
    approved = bool(state.get("risk_approved", False))
    share_count = int(state.get("share_count", 0) or 0)
    logger.info(
        "[Orchestrator] Risk routing decision risk_approved=%s share_count=%s",
        approved,
        share_count,
    )
    return "execute_trade" if approved and share_count > 0 else "end"


def build_graph():
    market_data_agent, strategy_agent, risk_agent, execution_agent = create_agents()
    market_data_node, strategy_node, risk_node, execution_node = build_nodes(
        market_data_agent,
        strategy_agent,
        risk_agent,
        execution_agent,
    )
    graph = StateGraph(TradingState)
    graph.add_node("fetch_market_data", market_data_node)
    graph.add_node("generate_signal", strategy_node)
    graph.add_node("risk_check", risk_node)
    graph.add_node("execute_trade", execution_node)

    graph.set_entry_point("fetch_market_data")
    graph.add_edge("fetch_market_data", "generate_signal")
    graph.add_edge("generate_signal", "risk_check")
    graph.add_conditional_edges(
        "risk_check",
        route_after_risk,
        {
            "execute_trade": "execute_trade",
            "end": END,
        },
    )
    graph.add_edge("execute_trade", END)
    return graph.compile()


async def run_trading_loop(ticker: str) -> TradingState:
    app = build_graph()
    initial_state: TradingState = {"ticker": ticker.strip().upper()}
    logger.info("[Orchestrator] Running trading loop for %s", initial_state["ticker"])
    final_state = await app.ainvoke(initial_state)
    logger.info("[Orchestrator] Final state for %s: %s", initial_state["ticker"], final_state)
    return final_state


def run_trading_loop_sync(ticker: str) -> TradingState:
    return asyncio.run(run_trading_loop(ticker))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM-driven trading platform using PydanticAI + LangGraph + MCP")
    parser.add_argument("ticker", type=str, help="US equity ticker symbol, for example AAPL")
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Print final state as pretty JSON",
    )
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
