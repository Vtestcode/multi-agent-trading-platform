from __future__ import annotations

import asyncio
import json
import os
import sys
import logging
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from langsmith import traceable
from langsmith.middleware import TracingMiddleware
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

load_dotenv(BACKEND_DIR / ".env")

from auth import (
    UserResponse,
    authenticate_local_user,
    create_access_token,
    create_local_user,
    get_current_user,
    get_optional_user,
    get_user_by_email,
    upsert_google_user,
    verify_google_id_token,
)
from db import Base, SessionLocal, engine, get_db
from day_session_manager import DaySessionManager
from integrations import (
    delete_broker_connection,
    get_broker_connection,
    get_provider_catalog,
    resolve_execution_credentials,
    serialize_broker_connection,
    list_broker_connections,
    upsert_broker_connection,
)
from history_store import (
    create_workflow_run,
    has_pending_execution_approval,
    list_workflow_runs,
    list_workflow_run_states,
    recent_unique_tickers,
    serialize_workflow_run,
)
from agents.copilot_agent import CopilotAgent
from main import run_trading_loop_sync
from models import User
from observability import configure_observability, get_langsmith_project, langsmith_enabled
configure_observability()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Multi Agent Equity Trading Platform",
    version="1.0.0",
    description="LLM-driven multi-agent equity trading platform with LangGraph orchestration.",
)

def _resolve_allowed_origins() -> list[str]:
    raw_value = os.getenv("CORS_ALLOW_ORIGINS", "*").strip()
    if raw_value.startswith("CORS_ALLOW_ORIGINS="):
        raw_value = raw_value.split("=", 1)[1].strip()

    parsed = [origin.strip() for origin in raw_value.split(",") if origin.strip()]
    if raw_value == "*" or not parsed:
        return ["*"]

    # Make local Next/FastAPI development less brittle across localhost/127.0.0.1.
    defaults = ["http://localhost:3000", "http://127.0.0.1:3000"]
    for origin in defaults:
        if origin not in parsed:
            parsed.append(origin)
    return parsed


allowed_origins = _resolve_allowed_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
if langsmith_enabled():
    app.add_middleware(TracingMiddleware)

app.state.last_workflow_state = None
app.state.copilot = CopilotAgent()
app.state.day_session_manager = None


@app.on_event("startup")
async def startup() -> None:
    Base.metadata.create_all(bind=engine)
    app.state.day_session_manager = DaySessionManager(run_session_callback=_run_day_session_cycle)
    await app.state.day_session_manager.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    manager = getattr(app.state, "day_session_manager", None)
    if manager is not None:
        await manager.stop()


class RunRequest(BaseModel):
    ticker: str | None = Field(default=None, min_length=1, max_length=10, description="Optional manual ticker override")
    confirm_execution: bool = False
    auto_execute: bool = False


class DaySessionRequest(BaseModel):
    ticker: str | None = Field(default=None, min_length=1, max_length=10)
    start_time: str = Field(default="09:30", pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(default="15:30", pattern=r"^\d{2}:\d{2}$")
    interval_minutes: int = Field(default=15, ge=1, le=240)
    timezone: str = Field(default="America/Chicago", min_length=3, max_length=64)
    auto_execute: bool = False


class HealthResponse(BaseModel):
    status: str
    platform: str
    deployment: str
    tracing: bool
    langsmith_project: str | None


class CopilotRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000, description="Operator question for the AI copilot")


class CopilotResponse(BaseModel):
    reply: str
    model: str
    has_workflow_state: bool
    action_taken: str | None = None
    action_result: dict | list | str | None = None


def _stream_event(payload: dict) -> str:
    return json.dumps(payload, default=str) + "\n"


def _validate_day_session_payload(payload: DaySessionRequest) -> None:
    try:
        ZoneInfo(payload.timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid timezone.") from exc
    if payload.end_time <= payload.start_time:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_time must be later than start_time for the same trading day.",
        )


async def _execute_workflow_for_user(
    *,
    ticker: str | None,
    confirm_execution: bool,
    auto_execute: bool,
    current_user: User | None,
    db: Session,
) -> dict:
    ticker = ticker.strip().upper() if ticker else None
    if confirm_execution:
        if current_user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Sign in is required before execution can be approved.",
            )
        if not has_pending_execution_approval(db, current_user, ticker):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Execution approval is only allowed for the most recent pending trade candidate. "
                    "Run analysis first, then approve the pending execution from the workspace."
                ),
            )
    if auto_execute and not confirm_execution and current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in is required before auto execution can be enabled.",
        )
    connection = get_broker_connection(db, current_user.id, "alpaca") if current_user else None
    if auto_execute and not confirm_execution and connection is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Connect an execution-capable provider before enabling auto execution.",
        )
    excluded_tickers = recent_unique_tickers(db, current_user, limit=3) if current_user and not ticker and not confirm_execution else []
    broker_connection = None
    broker_connection_summary = None
    broker_connection_error = None

    if connection:
        try:
            broker_connection = resolve_execution_credentials(connection)
            broker_connection_summary = serialize_broker_connection(connection)
        except Exception as exc:
            broker_connection_error = (
                "Connected broker credentials could not be loaded. "
                "Reconnect your broker account and try again."
            )
            broker_connection_summary = {
                "provider": connection.provider,
                "display_name": connection.provider.title(),
                "is_connected": False,
                "error": str(exc),
            }

    try:
        result = await run_in_threadpool(
            run_trading_loop_sync,
            ticker,
            broker_connection,
            broker_connection_summary,
            excluded_tickers,
            (confirm_execution or auto_execute) and broker_connection is not None,
        )
    except Exception as exc:
        logger.exception("Workflow execution failed")
        detail = str(exc) or "Workflow execution failed."
        if "401" in detail and "unauthorized" in detail.lower():
            detail = (
                "Connected Alpaca credentials were rejected. "
                "Reconnect your Alpaca paper-trading API key and secret, then try again."
            )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from exc

    result["current_user"] = UserResponse.from_model(current_user).model_dump() if current_user else None
    result["broker_connection_error"] = broker_connection_error
    result["excluded_tickers"] = excluded_tickers
    result["execution_confirmation_armed"] = confirm_execution and broker_connection is not None
    result["auto_execute_enabled"] = auto_execute and broker_connection is not None
    if current_user:
        run_record = create_workflow_run(db, current_user, result)
        result["history_id"] = run_record.id
    app.state.last_workflow_state = result
    return result


async def _run_day_session_cycle(user_id: int, ticker: str | None, auto_execute: bool) -> dict[str, Any]:
    db = SessionLocal()
    try:
        current_user = db.get(User, user_id)
        if current_user is None:
            raise RuntimeError("User not found for day session.")
        return await _execute_workflow_for_user(
            ticker=ticker,
            confirm_execution=False,
            auto_execute=auto_execute,
            current_user=current_user,
            db=db,
        )
    finally:
        db.close()


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)


class GoogleAuthRequest(BaseModel):
    credential: str = Field(min_length=10)


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class BrokerConnectionRequest(BaseModel):
    provider: str = Field(min_length=3, max_length=50)
    api_key: str = Field(min_length=4, max_length=255)
    secret_key: str | None = Field(default=None, max_length=255)
    environment: str = Field(default="paper", min_length=4, max_length=50)
    label: str | None = Field(default=None, max_length=255)


class BrokerConnectionResponse(BaseModel):
    provider: str
    display_name: str
    category: str
    supports_execution: bool
    auth_fields: list[str]
    environment: str
    label: str | None = None
    is_connected: bool
    api_key_preview: str


class ProviderCatalogResponse(BaseModel):
    provider: str
    display_name: str
    category: str
    supports_execution: bool
    default_environment: str
    auth_fields: list[str]
    description: str


class WorkflowRunResponse(BaseModel):
    id: int
    ticker: str
    scanner_mode: str
    signal: str | None = None
    execution_status: str | None = None
    risk_approved: bool | None = None
    strategy_confidence: str | None = None
    summary: str | None = None
    created_at: str | None = None


class DaySessionResponse(BaseModel):
    enabled: bool
    status: str
    ticker: str | None = None
    start_time: str
    end_time: str
    interval_minutes: int
    timezone: str
    auto_execute: bool
    created_at: str
    last_run_at: str | None = None
    next_run_at: str | None = None
    last_error: str | None = None
    run_count: int
    active_run: bool
    last_window_date: str | None = None
    last_result: dict | None = None


@app.get("/api/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    return HealthResponse(
        status="ok",
        platform="Multi Agent Equity Trading Platform",
        deployment="backend",
        tracing=langsmith_enabled(),
        langsmith_project=get_langsmith_project() if langsmith_enabled() else None,
    )


@app.post("/api/auth/register", response_model=AuthResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> AuthResponse:
    if get_user_by_email(db, payload.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")
    user = create_local_user(db, payload.email, payload.password, payload.full_name)
    token = create_access_token(user.email)
    return AuthResponse(access_token=token, user=UserResponse.from_model(user))


@app.post("/api/auth/login", response_model=AuthResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> AuthResponse:
    user = authenticate_local_user(db, payload.email, payload.password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    token = create_access_token(user.email)
    return AuthResponse(access_token=token, user=UserResponse.from_model(user))


@app.post("/api/auth/google", response_model=AuthResponse)
def google_auth(payload: GoogleAuthRequest, db: Session = Depends(get_db)) -> AuthResponse:
    token_info = verify_google_id_token(payload.credential)
    user = upsert_google_user(db, token_info)
    token = create_access_token(user.email)
    return AuthResponse(access_token=token, user=UserResponse.from_model(user))


@app.get("/api/auth/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.from_model(current_user)


@app.get("/api/integrations/providers", response_model=list[ProviderCatalogResponse])
def list_integration_providers() -> list[ProviderCatalogResponse]:
    return [ProviderCatalogResponse(**provider) for provider in get_provider_catalog()]


@app.get("/api/integrations", response_model=list[BrokerConnectionResponse])
def list_integrations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[BrokerConnectionResponse]:
    return [
        BrokerConnectionResponse(**serialized)
        for serialized in [serialize_broker_connection(connection) for connection in list_broker_connections(db, current_user.id)]
        if serialized
    ]


@app.get("/api/integrations/{provider}", response_model=BrokerConnectionResponse | None)
def get_integration(
    provider: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BrokerConnectionResponse | None:
    connection = get_broker_connection(db, current_user.id, provider)
    serialized = serialize_broker_connection(connection)
    return BrokerConnectionResponse(**serialized) if serialized else None


@app.post("/api/integrations/{provider}", response_model=BrokerConnectionResponse)
def connect_integration(
    provider: str,
    payload: BrokerConnectionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BrokerConnectionResponse:
    connection = upsert_broker_connection(
        db=db,
        user=current_user,
        provider=provider,
        api_key=payload.api_key,
        secret_key=payload.secret_key or "",
        environment=payload.environment,
        label=payload.label,
    )
    return BrokerConnectionResponse(**serialize_broker_connection(connection))


@app.delete("/api/integrations/{provider}")
def disconnect_integration(
    provider: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    deleted = delete_broker_connection(db, current_user, provider)
    return {"deleted": deleted}


@app.get("/api/history", response_model=list[WorkflowRunResponse])
def get_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[WorkflowRunResponse]:
    return [WorkflowRunResponse(**serialize_workflow_run(run)) for run in list_workflow_runs(db, current_user)]


@app.post("/api/run")
@traceable(name="api_run_workflow")
async def run_workflow(
    payload: RunRequest,
    current_user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> dict:
    return await _execute_workflow_for_user(
        ticker=payload.ticker,
        confirm_execution=bool(payload.confirm_execution),
        auto_execute=bool(payload.auto_execute),
        current_user=current_user,
        db=db,
    )


@app.get("/api/day-session", response_model=DaySessionResponse | None)
def get_day_session(current_user: User = Depends(get_current_user)) -> DaySessionResponse | None:
    snapshot = app.state.day_session_manager.snapshot_for_user(current_user.id)
    return DaySessionResponse(**snapshot) if snapshot else None


@app.post("/api/day-session", response_model=DaySessionResponse)
def start_day_session(
    payload: DaySessionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DaySessionResponse:
    _validate_day_session_payload(payload)
    if payload.auto_execute and get_broker_connection(db, current_user.id, "alpaca") is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Connect an execution-capable provider before enabling auto execution for a day session.",
        )
    snapshot = app.state.day_session_manager.upsert_session(
        user_id=current_user.id,
        ticker=payload.ticker,
        start_time=payload.start_time,
        end_time=payload.end_time,
        interval_minutes=payload.interval_minutes,
        timezone=payload.timezone,
        auto_execute=payload.auto_execute,
    )
    return DaySessionResponse(**snapshot)


@app.delete("/api/day-session", response_model=DaySessionResponse | None)
def stop_day_session(current_user: User = Depends(get_current_user)) -> DaySessionResponse | None:
    snapshot = app.state.day_session_manager.stop_session(current_user.id)
    return DaySessionResponse(**snapshot) if snapshot else None


@app.post("/api/copilot", response_model=CopilotResponse)
@traceable(name="api_copilot_chat")
async def copilot_chat(
    payload: CopilotRequest,
    current_user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> CopilotResponse:
    workflow_state = app.state.last_workflow_state
    history_states = list_workflow_run_states(db, current_user, limit=8) if current_user else []
    if workflow_state is None and history_states:
        workflow_state = history_states[0]
    copilot_result = await app.state.copilot.answer(payload.message, workflow_state, db=db, current_user=current_user)
    action_result = copilot_result.get("action_result")
    if isinstance(action_result, dict) and action_result.get("ticker") and (
        action_result.get("signal") or action_result.get("scanner_summary") or action_result.get("market_data")
    ):
        app.state.last_workflow_state = action_result
        if current_user and copilot_result.get("action_taken") in {"run_workflow", "execute_trade"}:
            run_record = create_workflow_run(db, current_user, action_result)
            action_result["history_id"] = run_record.id
    return CopilotResponse(
        reply=str(copilot_result.get("reply") or ""),
        model=app.state.copilot.model_name,
        has_workflow_state=workflow_state is not None or bool(history_states),
        action_taken=copilot_result.get("action_taken"),
        action_result=action_result,
    )


@app.post("/api/copilot/stream")
@traceable(name="api_copilot_chat_stream")
async def copilot_chat_stream(
    payload: CopilotRequest,
    current_user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    workflow_state = app.state.last_workflow_state
    history_states = list_workflow_run_states(db, current_user, limit=8) if current_user else []
    if workflow_state is None and history_states:
        workflow_state = history_states[0]

    async def event_generator():
        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        async def emit(event: dict) -> None:
            await queue.put(event)

        async def run_copilot() -> None:
            try:
                copilot_result = await app.state.copilot.stream_answer(
                    payload.message,
                    workflow_state,
                    db=db,
                    current_user=current_user,
                    emit=emit,
                )
                action_result = copilot_result.get("action_result")
                if isinstance(action_result, dict) and action_result.get("ticker") and (
                    action_result.get("signal") or action_result.get("scanner_summary") or action_result.get("market_data")
                ):
                    app.state.last_workflow_state = action_result
                    if current_user and copilot_result.get("action_taken") in {"run_workflow", "execute_trade"}:
                        run_record = create_workflow_run(db, current_user, action_result)
                        action_result["history_id"] = run_record.id
                await queue.put(
                    {
                        "type": "complete",
                        "model": app.state.copilot.model_name,
                        "has_workflow_state": workflow_state is not None or bool(history_states),
                        "action_taken": copilot_result.get("action_taken"),
                        "action_result": action_result,
                    }
                )
            except Exception as exc:
                logger.exception("Copilot streaming request failed")
                await queue.put({"type": "error", "message": str(exc) or "Copilot request failed."})
            finally:
                await queue.put(None)

        producer = asyncio.create_task(run_copilot())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield _stream_event(event)
        finally:
            if not producer.done():
                producer.cancel()

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "Multi Agent Equity Trading Platform API",
        "status": "ok",
    }
