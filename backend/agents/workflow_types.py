from __future__ import annotations

from typing import Any, Dict, Literal, TypedDict


class ValidationReport(TypedDict, total=False):
    agent: str
    passed: bool
    iteration: int
    summary: str
    issues: list[str]


class ToolInvocation(TypedDict, total=False):
    tool_name: str
    agent: str
    status: str
    input: Dict[str, Any]
    output_preview: Dict[str, Any] | list[Any] | str | None


class TradingState(TypedDict, total=False):
    ticker: str
    manual_ticker: str | None
    scanner_mode: Literal["auto", "manual"]
    selected_ticker: str
    scan_candidates: list[Dict[str, Any]]
    scanner_summary: str
    market_data: Dict[str, Any]
    market_context: Dict[str, Any]
    research_summary: str
    research_sentiment: Literal["bullish", "bearish", "mixed", "neutral"]
    research_updates: list[str]
    research_catalysts: list[str]
    research_risk_flags: list[str]
    research_inputs: Dict[str, Any]
    signal: Literal["BUY", "SELL", "HOLD"]
    strategy_reason: str
    strategy_confidence: float
    strategy_risks: list[str]
    strategy_inputs: Dict[str, Any]
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
    allow_execution: bool
    excluded_tickers: list[str]
    tool_inventory: Dict[str, list[str]]
    tool_history: list[ToolInvocation]
    orchestration_trace: list[str]
    validation_reports: list[ValidationReport]
    validation_status: str
    coordinator_summary: str
    execution_requires_confirmation: bool
