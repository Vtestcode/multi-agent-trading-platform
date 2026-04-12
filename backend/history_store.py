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
    return f"{ticker} · {scanner_mode} · {signal} · {execution_status}"
