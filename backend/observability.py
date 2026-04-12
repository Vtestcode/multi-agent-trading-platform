from __future__ import annotations

import logging
import os

import langsmith as ls
from langsmith.integrations.otel import configure as configure_langsmith_otel
from pydantic_ai import Agent

logger = logging.getLogger(__name__)

DEFAULT_LANGSMITH_PROJECT = "multi-agent-equity-trading-platform"
_configured = False


def langsmith_enabled() -> bool:
    return os.getenv("LANGSMITH_TRACING", "false").lower() == "true" and bool(os.getenv("LANGSMITH_API_KEY"))


def get_langsmith_project() -> str:
    return os.getenv("LANGSMITH_PROJECT", DEFAULT_LANGSMITH_PROJECT)


def configure_observability() -> None:
    global _configured
    if _configured or not langsmith_enabled():
        return

    project_name = get_langsmith_project()
    configure_langsmith_otel(project_name=project_name)
    Agent.instrument_all()
    _configured = True
    logger.info("[Observability] LangSmith tracing enabled for project=%s", project_name)


def workflow_tracing_context(ticker: str):
    return ls.tracing_context(
        enabled=langsmith_enabled(),
        project_name=get_langsmith_project(),
        tags=["backend", "trading-workflow", "langgraph", "pydantic-ai"],
        metadata={
            "ticker": ticker,
            "service": "backend",
            "workflow": "multi-agent-equity-trading-platform",
        },
    )
