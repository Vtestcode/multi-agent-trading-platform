from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


class User(Base):
    __tablename__ = "trading_platform_users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hashed_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_provider: Mapped[str] = mapped_column(String(50), default="local")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    broker_connections: Mapped[list["BrokerConnection"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    workflow_runs: Mapped[list["WorkflowRun"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class BrokerConnection(Base):
    __tablename__ = "trading_platform_broker_connections"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_broker_connection_user_provider"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("trading_platform_users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    environment: Mapped[str] = mapped_column(String(50), default="paper")
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    encrypted_secret_key: Mapped[str] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped[User] = relationship(back_populates="broker_connections")


class WorkflowRun(Base):
    __tablename__ = "trading_platform_workflow_runs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("trading_platform_users.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    scanner_mode: Mapped[str] = mapped_column(String(20), default="auto")
    signal: Mapped[str | None] = mapped_column(String(20), nullable=True)
    execution_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    risk_approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    strategy_confidence: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    workflow_state_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    user: Mapped[User] = relationship(back_populates="workflow_runs")
