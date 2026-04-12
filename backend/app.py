from __future__ import annotations

import os
import sys
import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from langsmith import traceable
from langsmith.middleware import TracingMiddleware
from sqlalchemy.orm import Session

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
from db import Base, engine, get_db
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


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)


class RunRequest(BaseModel):
    ticker: str | None = Field(default=None, min_length=1, max_length=10, description="Optional manual ticker override")
    confirm_execution: bool = False


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
def run_workflow(
    payload: RunRequest,
    current_user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> dict:
    ticker = payload.ticker.strip().upper() if payload.ticker else None
    confirm_execution = bool(payload.confirm_execution)
    connection = get_broker_connection(db, current_user.id, "alpaca") if current_user else None
    excluded_tickers = recent_unique_tickers(db, current_user, limit=3) if current_user and not ticker else []
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
        result = run_trading_loop_sync(
            ticker=ticker,
            broker_connection=broker_connection,
            broker_connection_summary=broker_connection_summary,
            excluded_tickers=excluded_tickers,
            allow_execution=confirm_execution and broker_connection is not None,
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
    if current_user:
        run_record = create_workflow_run(db, current_user, result)
        result["history_id"] = run_record.id
    app.state.last_workflow_state = result
    return result


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
    if isinstance(action_result, dict) and action_result.get("ticker") and action_result.get("signal"):
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


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "Multi Agent Equity Trading Platform API",
        "status": "ok",
    }
