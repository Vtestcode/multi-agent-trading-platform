from __future__ import annotations

import json
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from models import User, WorkflowRun


def create_workflow_run(db: Session, user: User, state: dict[str, Any]) -> WorkflowRun:
    run = WorkflowRun(
        user_id=user.id,
        ticker=str(state.get("ticker") or state.get("selected_ticker") or "UNKNOWN"),
        scanner_mode=str(state.get("scanner_mode") or "auto"),
        signal=state.get("signal"),
        execution_status=state.get("execution_status"),
        risk_approved=state.get("risk_approved"),
        strategy_confidence=_serialize_confidence(state.get("strategy_confidence")),
        summary=_build_summary(state),
        workflow_state_json=json.dumps(state, default=str),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def list_workflow_runs(db: Session, user: User, limit: int = 20) -> list[WorkflowRun]:
    stmt = (
        select(WorkflowRun)
        .where(WorkflowRun.user_id == user.id)
        .order_by(desc(WorkflowRun.created_at), desc(WorkflowRun.id))
        .limit(limit)
    )
    return list(db.scalars(stmt))


def recent_unique_tickers(db: Session, user: User, limit: int = 3) -> list[str]:
    runs = list_workflow_runs(db, user, limit=limit * 4)
    tickers: list[str] = []
    for run in runs:
        ticker = (run.ticker or "").strip().upper()
        if not ticker or ticker in tickers:
            continue
        tickers.append(ticker)
        if len(tickers) >= limit:
            break
    return tickers


def serialize_workflow_run(run: WorkflowRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "ticker": run.ticker,
        "scanner_mode": run.scanner_mode,
        "signal": run.signal,
        "execution_status": run.execution_status,
        "risk_approved": run.risk_approved,
        "strategy_confidence": run.strategy_confidence,
        "summary": run.summary,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


def deserialize_workflow_run(run: WorkflowRun) -> dict[str, Any]:
    state: dict[str, Any] = {}
    if run.workflow_state_json:
        try:
            state = json.loads(run.workflow_state_json)
        except json.JSONDecodeError:
            state = {"raw_state": run.workflow_state_json}
    state.update(
        {
            "id": run.id,
            "ticker": run.ticker,
            "scanner_mode": run.scanner_mode,
            "signal": run.signal,
            "execution_status": run.execution_status,
            "risk_approved": run.risk_approved,
            "strategy_confidence": run.strategy_confidence,
            "summary": run.summary,
            "created_at": run.created_at.isoformat() if run.created_at else None,
        }
    )
    return state


def list_workflow_run_states(db: Session | None, user: User | None, limit: int = 20) -> list[dict[str, Any]]:
    if db is None or user is None:
        return []
    return [deserialize_workflow_run(run) for run in list_workflow_runs(db, user, limit=limit)]


def latest_workflow_run_state(db: Session | None, user: User | None) -> dict[str, Any] | None:
    runs = list_workflow_run_states(db, user, limit=1)
    return runs[0] if runs else None


def has_pending_execution_approval(db: Session | None, user: User | None, ticker: str | None) -> bool:
    if db is None or user is None or not ticker:
        return False
    latest_run = latest_workflow_run_state(db, user)
    if not latest_run:
        return False
    return (
        str(latest_run.get("ticker") or "").strip().upper() == ticker.strip().upper()
        and str(latest_run.get("execution_status") or "") == "AWAITING_CONFIRMATION"
        and bool(latest_run.get("risk_approved"))
    )


def summarize_workflow_runs(runs: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for run in runs[:limit]:
        summaries.append(
            {
                "id": run.get("id"),
                "ticker": run.get("ticker"),
                "signal": run.get("signal"),
                "execution_status": run.get("execution_status"),
                "risk_approved": run.get("risk_approved"),
                "summary": run.get("summary"),
                "created_at": run.get("created_at"),
            }
        )
    return summaries


def _serialize_confidence(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _build_summary(state: dict[str, Any]) -> str:
    ticker = str(state.get("ticker") or state.get("selected_ticker") or "UNKNOWN")
    signal = str(state.get("signal") or "PENDING")
    execution_status = str(state.get("execution_status") or "PENDING")
    scanner_mode = str(state.get("scanner_mode") or "auto")
    return " | ".join([ticker, scanner_mode, signal, execution_status])
