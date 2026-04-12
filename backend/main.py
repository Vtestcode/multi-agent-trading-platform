from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Literal, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langsmith import traceable

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agents.execution_agent import ExecutionAgent
from agents.market_data_agent import MarketDataAgent
from agents.risk_agent import RiskAgent
from agents.scanner_agent import MarketScannerAgent
from agents.strategy_agent import StrategyAgent
from observability import configure_observability, workflow_tracing_context

load_dotenv()
configure_observability()


class TradingState(TypedDict, total=False):
    ticker: str
    manual_ticker: str | None
    scanner_mode: Literal["auto", "manual"]
    selected_ticker: str
    scan_candidates: list[Dict[str, Any]]
    scanner_summary: str
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
    broker_connection: Dict[str, Any] | None
    broker_connection_summary: Dict[str, Any] | None
    excluded_tickers: list[str]


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("trading_system")


def create_agents() -> tuple[MarketDataAgent, MarketScannerAgent, StrategyAgent, RiskAgent, ExecutionAgent]:
    market_data_agent = MarketDataAgent()
    return (
        market_data_agent,
        MarketScannerAgent(market_data_agent=market_data_agent),
        StrategyAgent(),
        RiskAgent(),
        ExecutionAgent(),
    )


def build_nodes(
    market_data_agent: MarketDataAgent,
    scanner_agent: MarketScannerAgent,
    strategy_agent: StrategyAgent,
    risk_agent: RiskAgent,
    execution_agent: ExecutionAgent,
):
    async def scanner_node(state: TradingState) -> Dict[str, Any]:
        manual_ticker = state.get("manual_ticker")
        excluded_tickers = state.get("excluded_tickers") or []
        logger.info(
            "[Orchestrator] Starting scanner node manual_ticker=%s excluded_tickers=%s",
            manual_ticker,
            excluded_tickers,
        )
        return await scanner_agent.run(manual_ticker=manual_ticker, excluded_tickers=excluded_tickers)

    async def market_data_node(state: TradingState) -> Dict[str, Any]:
        ticker = state.get("selected_ticker") or state["ticker"]
        existing_market_data = state.get("market_data")
        if existing_market_data:
            logger.info("[Orchestrator] Reusing scanner market data for %s", ticker)
            return {"ticker": ticker, "market_data": existing_market_data}
        logger.info("[Orchestrator] Starting market data node for %s", ticker)
        market_state = await market_data_agent.run(ticker=ticker)
        return {"ticker": ticker, **market_state}

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

    return scanner_node, market_data_node, strategy_node, risk_node, execution_node


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
    market_data_agent, scanner_agent, strategy_agent, risk_agent, execution_agent = create_agents()
    scanner_node, market_data_node, strategy_node, risk_node, execution_node = build_nodes(
        market_data_agent,
        scanner_agent,
        strategy_agent,
        risk_agent,
        execution_agent,
    )
    graph = StateGraph(TradingState)
    graph.add_node("scan_market", scanner_node)
    graph.add_node("fetch_market_data", market_data_node)
    graph.add_node("generate_signal", strategy_node)
    graph.add_node("risk_check", risk_node)
    graph.add_node("execute_trade", execution_node)

    graph.set_entry_point("scan_market")
    graph.add_edge("scan_market", "fetch_market_data")
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


@traceable(name="run_trading_loop")
async def run_trading_loop(
    ticker: str | None = None,
    broker_connection: Dict[str, Any] | None = None,
    broker_connection_summary: Dict[str, Any] | None = None,
    excluded_tickers: list[str] | None = None,
) -> TradingState:
    app = build_graph()
    normalized_ticker = ticker.strip().upper() if ticker else None
    initial_state: TradingState = {
        "manual_ticker": normalized_ticker,
        "broker_connection": broker_connection,
        "broker_connection_summary": broker_connection_summary,
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
) -> TradingState:
    return asyncio.run(run_trading_loop(ticker, broker_connection, broker_connection_summary, excluded_tickers))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM-driven trading platform using PydanticAI + LangGraph + MCP")
    parser.add_argument("ticker", nargs="?", type=str, help="Optional manual ticker override, for example AAPL")
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
