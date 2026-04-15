from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


RunSessionCallback = Callable[[int, str | None, bool], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class DaySession:
    user_id: int
    ticker: str | None
    start_time: str
    end_time: str
    interval_minutes: int
    timezone: str
    auto_execute: bool
    enabled: bool = True
    status: str = "scheduled"
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    last_error: str | None = None
    last_result: dict[str, Any] | None = None
    run_count: int = 0
    active_run: bool = False
    last_window_date: str | None = None


class DaySessionManager:
    def __init__(self, run_session_callback: RunSessionCallback, poll_seconds: float = 15.0) -> None:
        self.run_session_callback = run_session_callback
        self.poll_seconds = poll_seconds
        self._sessions: dict[int, DaySession] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="day-session-manager")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    def upsert_session(
        self,
        *,
        user_id: int,
        ticker: str | None,
        start_time: str,
        end_time: str,
        interval_minutes: int,
        timezone: str,
        auto_execute: bool,
    ) -> dict[str, Any]:
        session = DaySession(
            user_id=user_id,
            ticker=ticker.strip().upper() if ticker else None,
            start_time=start_time,
            end_time=end_time,
            interval_minutes=max(1, interval_minutes),
            timezone=timezone,
            auto_execute=auto_execute,
        )
        self._sessions[user_id] = session
        self._refresh_schedule(session)
        return self.snapshot_for_user(user_id)

    def stop_session(self, user_id: int) -> dict[str, Any] | None:
        session = self._sessions.get(user_id)
        if session is None:
            return None
        session.enabled = False
        session.status = "stopped"
        session.next_run_at = None
        return self._snapshot(session)

    def snapshot_for_user(self, user_id: int) -> dict[str, Any] | None:
        session = self._sessions.get(user_id)
        if session is None:
            return None
        return self._snapshot(session)

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            await self._tick()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                continue

    async def _tick(self) -> None:
        for session in list(self._sessions.values()):
            if not session.enabled:
                continue

            try:
                self._refresh_schedule(session)
                now_local = self._now_for_session(session)
                current_start, current_end = self._window_bounds(session, now_local)
                is_window_open = current_start <= now_local <= current_end

                if not is_window_open:
                    session.status = "scheduled"
                    continue

                if session.active_run:
                    session.status = "running"
                    continue

                if session.last_run_at is not None:
                    last_run_local = session.last_run_at.astimezone(ZoneInfo(session.timezone))
                    seconds_since_last_run = (now_local - last_run_local).total_seconds()
                    if seconds_since_last_run < session.interval_minutes * 60:
                        session.status = "running"
                        session.next_run_at = last_run_local + timedelta(minutes=session.interval_minutes)
                        continue

                session.active_run = True
                session.status = "running"
                result = await self.run_session_callback(session.user_id, session.ticker, session.auto_execute)
                session.last_result = result
                session.last_error = None
                session.last_run_at = datetime.now(tz=ZoneInfo("UTC"))
                session.last_window_date = now_local.date().isoformat()
                session.run_count += 1
                session.next_run_at = now_local + timedelta(minutes=session.interval_minutes)
            except Exception as exc:
                logger.exception("Day session tick failed for user_id=%s", session.user_id)
                session.last_error = str(exc)
                session.status = "error"
            finally:
                session.active_run = False

    def _refresh_schedule(self, session: DaySession) -> None:
        now_local = self._now_for_session(session)
        if now_local.weekday() >= 5:
            session.next_run_at = self._next_weekday_start(session, now_local + timedelta(days=1))
            if session.status not in {"error", "stopped"}:
                session.status = "scheduled"
            return
        current_start, current_end = self._window_bounds(session, now_local)
        if now_local < current_start:
            session.next_run_at = current_start
            if session.status not in {"error", "stopped"}:
                session.status = "scheduled"
            return
        if current_start <= now_local <= current_end:
            if session.last_run_at is None:
                session.next_run_at = now_local
            else:
                last_run_local = session.last_run_at.astimezone(ZoneInfo(session.timezone))
                session.next_run_at = last_run_local + timedelta(minutes=session.interval_minutes)
            if session.status not in {"error", "stopped"}:
                session.status = "running"
            return

        next_start = self._next_weekday_start(session, now_local + timedelta(days=1))
        session.next_run_at = next_start
        if session.status not in {"error", "stopped"}:
            session.status = "scheduled"

    @staticmethod
    def _now_for_session(session: DaySession) -> datetime:
        return datetime.now(tz=ZoneInfo(session.timezone))

    @staticmethod
    def _parse_hhmm(value: str) -> time:
        hour_str, minute_str = value.split(":", 1)
        return time(hour=int(hour_str), minute=int(minute_str))

    def _window_bounds(self, session: DaySession, now_local: datetime) -> tuple[datetime, datetime]:
        zone = ZoneInfo(session.timezone)
        start_parts = self._parse_hhmm(session.start_time)
        end_parts = self._parse_hhmm(session.end_time)
        start_at = datetime.combine(now_local.date(), start_parts, tzinfo=zone)
        end_at = datetime.combine(now_local.date(), end_parts, tzinfo=zone)
        if end_at <= start_at:
            end_at = start_at + timedelta(minutes=1)
        return start_at, end_at

    def _next_weekday_start(self, session: DaySession, starting_from: datetime) -> datetime:
        zone = ZoneInfo(session.timezone)
        candidate = starting_from.astimezone(zone)
        start_parts = self._parse_hhmm(session.start_time)
        while candidate.weekday() >= 5:
            candidate = candidate + timedelta(days=1)
        return datetime.combine(candidate.date(), start_parts, tzinfo=zone)

    def _snapshot(self, session: DaySession) -> dict[str, Any]:
        return {
            "enabled": session.enabled,
            "status": session.status,
            "ticker": session.ticker,
            "start_time": session.start_time,
            "end_time": session.end_time,
            "interval_minutes": session.interval_minutes,
            "timezone": session.timezone,
            "auto_execute": session.auto_execute,
            "created_at": session.created_at.isoformat(),
            "last_run_at": session.last_run_at.isoformat() if session.last_run_at else None,
            "next_run_at": session.next_run_at.isoformat() if session.next_run_at else None,
            "last_error": session.last_error,
            "run_count": session.run_count,
            "active_run": session.active_run,
            "last_window_date": session.last_window_date,
            "last_result": {
                "ticker": (session.last_result or {}).get("ticker"),
                "signal": (session.last_result or {}).get("signal"),
                "execution_status": (session.last_result or {}).get("execution_status"),
                "summary": (session.last_result or {}).get("summary"),
            }
            if session.last_result
            else None,
        }
