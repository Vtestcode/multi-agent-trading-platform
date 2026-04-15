"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";
const TOKEN_STORAGE_KEY = "trading_platform_access_token";
const AUTO_EXECUTE_STORAGE_KEY = "trading_platform_auto_execute";
const DAY_SESSION_POLL_MS = 15000;

function getApiBaseCandidates(primaryBaseUrl) {
  const normalized = (primaryBaseUrl || DEFAULT_API_BASE_URL).replace(/\/$/, "");
  const candidates = [normalized];

  if (normalized.includes("127.0.0.1:8000")) {
    candidates.push(normalized.replace("127.0.0.1:8000", "localhost:8000"));
  } else if (normalized.includes("localhost:8000")) {
    candidates.push(normalized.replace("localhost:8000", "127.0.0.1:8000"));
  }

  return [...new Set(candidates)];
}

function shortDateTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function statusClass(value) {
  const normalized = String(value || "").toLowerCase();
  if (["buy", "approved", "submitted", "running"].includes(normalized)) return normalized;
  if (["sell", "rejected", "failed", "error", "stopped"].includes(normalized)) return normalized;
  if (["hold", "skipped", "pending", "scheduled", "off"].includes(normalized)) return normalized;
  return "neutral";
}

function Pill({ label, value }) {
  return <span className={`pill ${statusClass(value)}`}>{`${label}: ${value}`}</span>;
}

export default function DaySessionPage() {
  const apiBaseUrl = useMemo(
    () => (process.env.NEXT_PUBLIC_API_BASE_URL || DEFAULT_API_BASE_URL).replace(/\/$/, ""),
    []
  );
  const apiBaseCandidates = useMemo(() => getApiBaseCandidates(apiBaseUrl), [apiBaseUrl]);
  const [token, setToken] = useState(null);
  const [autoExecutionEnabled, setAutoExecutionEnabled] = useState(false);
  const [daySession, setDaySession] = useState(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [form, setForm] = useState({
    ticker: "",
    startTime: "09:30",
    endTime: "15:30",
    intervalMinutes: 15,
    timezone: "America/Chicago",
  });

  useEffect(() => {
    setToken(window.localStorage.getItem(TOKEN_STORAGE_KEY));
    setAutoExecutionEnabled(window.localStorage.getItem(AUTO_EXECUTE_STORAGE_KEY) === "true");
    const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (browserTimezone) {
      setForm((current) => ({ ...current, timezone: browserTimezone }));
    }
  }, []);

  async function fetchWithFallback(path, options = {}) {
    let lastError = null;
    for (const baseUrl of apiBaseCandidates) {
      try {
        return await fetch(`${baseUrl}${path}`, options);
      } catch (fetchError) {
        lastError = fetchError;
      }
    }
    throw lastError || new Error("Failed to fetch");
  }

  async function apiFetch(path, options = {}) {
    const response = await fetchWithFallback(path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(options.headers || {}),
      },
    });
    return response;
  }

  useEffect(() => {
    let intervalId = null;

    async function loadDaySession() {
      if (!token) {
        setDaySession(null);
        return;
      }

      try {
        const response = await apiFetch("/api/day-session");
        if (!response.ok) {
          throw new Error((await response.text()) || "Could not load day session");
        }
        const payload = await response.json();
        setDaySession(payload);
        setError("");
        if (payload) {
          setForm((current) => ({
            ...current,
            ticker: payload.ticker || "",
            startTime: payload.start_time || current.startTime,
            endTime: payload.end_time || current.endTime,
            intervalMinutes: payload.interval_minutes || current.intervalMinutes,
            timezone: payload.timezone || current.timezone,
          }));
        }
      } catch (loadError) {
        setDaySession(null);
        setError(String(loadError.message || loadError));
      }
    }

    void loadDaySession();
    if (token) {
      intervalId = window.setInterval(() => {
        void loadDaySession();
      }, DAY_SESSION_POLL_MS);
    }

    return () => {
      if (intervalId) {
        window.clearInterval(intervalId);
      }
    };
  }, [token, apiBaseCandidates]);

  async function startSession() {
    setPending(true);
    try {
      const response = await apiFetch("/api/day-session", {
        method: "POST",
        body: JSON.stringify({
          ticker: form.ticker.trim().toUpperCase() || null,
          start_time: form.startTime,
          end_time: form.endTime,
          interval_minutes: Number(form.intervalMinutes) || 15,
          timezone: form.timezone,
          auto_execute: autoExecutionEnabled,
        }),
      });
      if (!response.ok) {
        throw new Error((await response.text()) || "Could not save day session");
      }
      const payload = await response.json();
      setDaySession(payload);
      setError("");
    } catch (saveError) {
      setError(String(saveError.message || saveError));
    } finally {
      setPending(false);
    }
  }

  async function stopSession() {
    setPending(true);
    try {
      const response = await apiFetch("/api/day-session", { method: "DELETE" });
      if (!response.ok) {
        throw new Error((await response.text()) || "Could not stop day session");
      }
      const payload = await response.json();
      setDaySession(payload);
      setError("");
    } catch (stopError) {
      setError(String(stopError.message || stopError));
    } finally {
      setPending(false);
    }
  }

  const sessionStatus = daySession?.enabled ? String(daySession.status || "scheduled").toUpperCase() : "OFF";
  const lastResultSummary = daySession?.last_result
    ? `${daySession.last_result.ticker || "--"} | ${daySession.last_result.signal || "PENDING"} | ${daySession.last_result.execution_status || "PENDING"}`
    : "No session runs yet.";

  return (
    <main className="help-shell history-shell day-session-shell">
      <section className="topbar">
        <div className="topbar-copy">
          <p className="eyebrow">Workspace</p>
          <h1 className="topbar-title">Intraday automation window with integrated oversight.</h1>
        </div>
        <div className="topbar-actions">
          <Link href="/" className="help-link">
            Workspace
          </Link>
          <Link href="/history" className="help-link">
            History
          </Link>
          <Link href="/help" className="help-link">
            Help
          </Link>
        </div>
      </section>

      <section className="workspace-banner card">
        <div>
          <p className="section-kicker">Overview</p>
          <h3>Run the agent on a repeating schedule so it can open, monitor, and sell out of positions during your trading window.</h3>
        </div>
        <div className="banner-meta">
          <span>{daySession?.ticker || "Auto selection"}</span>
          <span>{sessionStatus}</span>
        </div>
      </section>

      {!token ? (
        <section className="help-card">
          <h2>Sign in required</h2>
          <p>Day trading sessions are account-specific. Sign in from the workspace to manage your intraday automation window.</p>
        </section>
      ) : (
        <section className="help-grid day-session-layout">
          <article className="help-card day-session-panel">
            <div className="day-session-header">
              <span>
                <strong>Session Controls</strong>
                <small>Configure one backend-managed day trading session for your account.</small>
              </span>
              <Pill label="Session" value={sessionStatus} />
            </div>

            <label className="day-session-field">
              <span>Session Ticker</span>
              <input
                type="text"
                value={form.ticker}
                maxLength={10}
                placeholder="Blank = auto-select"
                disabled={pending}
                onChange={(event) => setForm((current) => ({ ...current, ticker: event.target.value.toUpperCase() }))}
              />
            </label>

            <div className="day-session-grid">
              <label className="day-session-field">
                <span>Start</span>
                <input
                  type="time"
                  value={form.startTime}
                  disabled={pending}
                  onChange={(event) => setForm((current) => ({ ...current, startTime: event.target.value }))}
                />
              </label>
              <label className="day-session-field">
                <span>End</span>
                <input
                  type="time"
                  value={form.endTime}
                  disabled={pending}
                  onChange={(event) => setForm((current) => ({ ...current, endTime: event.target.value }))}
                />
              </label>
            </div>

            <div className="day-session-grid">
              <label className="day-session-field">
                <span>Every Minutes</span>
                <input
                  type="number"
                  min="1"
                  max="240"
                  value={form.intervalMinutes}
                  disabled={pending}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      intervalMinutes: Math.max(1, Math.min(240, Number(event.target.value) || 15)),
                    }))
                  }
                />
              </label>
              <label className="day-session-field">
                <span>Timezone</span>
                <input
                  type="text"
                  value={form.timezone}
                  disabled={pending}
                  onChange={(event) => setForm((current) => ({ ...current, timezone: event.target.value }))}
                />
              </label>
            </div>

            <div className="day-session-note">
              <strong>Auto Execute</strong>
              <p>{autoExecutionEnabled ? "Enabled from the workspace and will be used for this session." : "Disabled in the workspace, so session runs will stop at confirmation instead of submitting orders."}</p>
            </div>

            <div className="day-session-actions">
              <button type="button" className="day-session-button" onClick={() => void startSession()} disabled={pending}>
                {pending ? "Saving..." : daySession?.enabled ? "Update Session" : "Start Session"}
              </button>
              <button
                type="button"
                className="day-session-button is-secondary"
                onClick={() => void stopSession()}
                disabled={!daySession?.enabled || pending}
              >
                Stop Session
              </button>
            </div>

            {error ? <p className="session-error">{error}</p> : null}
          </article>

          <article className="help-card day-session-panel">
            <div className="day-session-header">
              <span>
                <strong>Live Status</strong>
                <small>Latest scheduler state from the backend.</small>
              </span>
            </div>

            <div className="history-page-meta day-session-meta">
              <span>{`Next run: ${shortDateTime(daySession?.next_run_at)}`}</span>
              <span>{`Last run: ${shortDateTime(daySession?.last_run_at)}`}</span>
              <span>{`Run count: ${daySession?.run_count ?? 0}`}</span>
              <span>{`Window date: ${daySession?.last_window_date || "--"}`}</span>
            </div>

            <div className="day-session-note">
              <strong>Latest Result</strong>
              <p>{daySession?.last_error || lastResultSummary}</p>
            </div>

            <div className="day-session-note">
              <strong>How it behaves</strong>
              <p>Runs are backend-managed, so the session continues independently of a single homepage run. BUY can open longs or cover shorts, and SELL can close longs or open shorts when the account allows shorting.</p>
            </div>
          </article>
        </section>
      )}
    </main>
  );
}
