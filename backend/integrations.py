from __future__ import annotations

import base64
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import BrokerConnection, User

PROVIDER_CATALOG: dict[str, dict[str, Any]] = {
    "alpaca": {
        "provider": "alpaca",
        "display_name": "Alpaca",
        "category": "broker",
        "supports_execution": True,
        "default_environment": "paper",
        "auth_fields": ["api_key", "secret_key"],
        "description": "Connect your Alpaca paper account for user-owned order execution.",
    },
    "polygon": {
        "provider": "polygon",
        "display_name": "Polygon",
        "category": "market_data",
        "supports_execution": False,
        "default_environment": "production",
        "auth_fields": ["api_key"],
        "description": "Connect personal Polygon market-data credentials for future user-scoped data access.",
    },
    "openai": {
        "provider": "openai",
        "display_name": "OpenAI",
        "category": "ai",
        "supports_execution": False,
        "default_environment": "production",
        "auth_fields": ["api_key"],
        "description": "Connect an OpenAI key for future user-owned copilot and model usage.",
    },
    "langsmith": {
        "provider": "langsmith",
        "display_name": "LangSmith",
        "category": "observability",
        "supports_execution": False,
        "default_environment": "production",
        "auth_fields": ["api_key"],
        "description": "Connect LangSmith for user-level traces, evaluations, and monitoring.",
    },
}


def _resolve_fernet() -> Fernet:
    secret = os.getenv("BROKER_CREDENTIALS_ENCRYPTION_KEY")
    if not secret:
        raise RuntimeError("BROKER_CREDENTIALS_ENCRYPTION_KEY is required for broker integrations")

    raw = secret.encode("utf-8")
    if len(raw) != 44:
        raw = base64.urlsafe_b64encode(raw.ljust(32, b"0")[:32])
    return Fernet(raw)


def encrypt_secret(value: str) -> str:
    return _resolve_fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    try:
        return _resolve_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Stored broker credentials could not be decrypted") from exc


def get_provider_catalog() -> list[dict[str, Any]]:
    return [dict(config) for config in PROVIDER_CATALOG.values()]


def get_provider_config(provider: str) -> dict[str, Any]:
    normalized_provider = provider.strip().lower()
    if normalized_provider not in PROVIDER_CATALOG:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported provider: {provider}")
    return PROVIDER_CATALOG[normalized_provider]


def get_broker_connection(db: Session, user_id: int, provider: str = "alpaca") -> BrokerConnection | None:
    return db.scalar(
        select(BrokerConnection).where(
            BrokerConnection.user_id == user_id,
            BrokerConnection.provider == provider,
            BrokerConnection.is_active.is_(True),
        )
    )


def list_broker_connections(db: Session, user_id: int) -> list[BrokerConnection]:
    return list(
        db.scalars(
            select(BrokerConnection).where(
                BrokerConnection.user_id == user_id,
                BrokerConnection.is_active.is_(True),
            )
        )
    )


def upsert_broker_connection(
    db: Session,
    user: User,
    provider: str,
    api_key: str,
    secret_key: str,
    environment: str = "paper",
    label: str | None = None,
) -> BrokerConnection:
    config = get_provider_config(provider)
    normalized_provider = config["provider"]

    connection = get_broker_connection(db, user.id, normalized_provider)
    if connection is None:
        connection = BrokerConnection(user_id=user.id, provider=normalized_provider)
        db.add(connection)

    connection.environment = environment or config["default_environment"]
    connection.label = label
    connection.encrypted_api_key = encrypt_secret(api_key.strip())
    connection.encrypted_secret_key = encrypt_secret(secret_key.strip() if secret_key else "")
    connection.is_active = True
    db.commit()
    db.refresh(connection)
    return connection


def delete_broker_connection(db: Session, user: User, provider: str = "alpaca") -> bool:
    connection = get_broker_connection(db, user.id, provider.strip().lower())
    if connection is None:
        return False
    db.delete(connection)
    db.commit()
    return True


def serialize_broker_connection(connection: BrokerConnection | None) -> dict[str, Any] | None:
    if connection is None:
        return None

    config = get_provider_config(connection.provider)
    api_key = decrypt_secret(connection.encrypted_api_key)
    return {
        "provider": connection.provider,
        "display_name": config["display_name"],
        "category": config["category"],
        "supports_execution": config["supports_execution"],
        "auth_fields": list(config["auth_fields"]),
        "environment": connection.environment,
        "label": connection.label,
        "is_connected": connection.is_active,
        "api_key_preview": f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) >= 8 else "configured",
    }


def resolve_execution_credentials(connection: BrokerConnection | None) -> dict[str, Any] | None:
    if connection is None:
        return None

    return {
        "provider": connection.provider,
        "environment": connection.environment,
        "api_key": decrypt_secret(connection.encrypted_api_key),
        "secret_key": decrypt_secret(connection.encrypted_secret_key),
    }
