"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";
const TOKEN_STORAGE_KEY = "trading_platform_access_token";

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
  if (["buy", "approved", "submitted"].includes(normalized)) return normalized;
  if (["sell", "rejected", "failed"].includes(normalized)) return normalized;
  if (["hold", "skipped", "pending"].includes(normalized)) return normalized;
  return "neutral";
}

function Pill({ label, value }) {
  return <span className={`pill ${statusClass(value)}`}>{`${label}: ${value}`}</span>;
}

export default function HistoryPage() {
  const apiBaseUrl = useMemo(
    () => (process.env.NEXT_PUBLIC_API_BASE_URL || DEFAULT_API_BASE_URL).replace(/\/$/, ""),
    []
  );
  const apiBaseCandidates = useMemo(() => getApiBaseCandidates(apiBaseUrl), [apiBaseUrl]);
  const [token, setToken] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    setToken(window.localStorage.getItem(TOKEN_STORAGE_KEY));
  }, []);

  useEffect(() => {
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

    async function loadHistory() {
      if (!token) {
        setLoading(false);
        return;
      }

      try {
        const response = await fetchWithFallback("/api/history", {
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
        });
        if (!response.ok) {
          throw new Error((await response.text()) || "Could not load history");
        }
        setHistory(await response.json());
      } catch (loadError) {
        setError(String(loadError.message || loadError));
      } finally {
        setLoading(false);
      }
    }

    loadHistory();
  }, [token, apiBaseCandidates]);

  return (
    <main className="help-shell history-shell">
      <div className="help-header">
        <div>
          <p className="eyebrow">History</p>
          <h1>Saved workflow runs</h1>
          <p className="help-subtitle">Review recent scans, signals, approvals, and execution outcomes for your account.</p>
        </div>
        <div className="history-header-actions">
          <Link href="/" className="help-link">
            Back To Workspace
          </Link>
          <Link href="/help" className="help-link">
            Help
          </Link>
        </div>
      </div>

      {!token ? (
        <section className="help-card">
          <h2>Sign in required</h2>
          <p>History is saved per user. Sign in from the workspace to view your past runs and executions.</p>
        </section>
      ) : loading ? (
        <section className="history-page-list">
          {[1, 2, 3].map((item) => (
            <article key={item} className="help-card history-page-row is-loading">
              <div className="metric-skeleton">
                <div className="skeleton-line" style={{ width: "24%" }} />
                <div className="skeleton-line" style={{ width: "62%" }} />
              </div>
            </article>
          ))}
        </section>
      ) : error ? (
        <section className="help-card">
          <h2>Could not load history</h2>
          <p>{error}</p>
        </section>
      ) : (
        <section className="history-page-list">
          {history.length ? (
            history.map((run) => (
              <article key={run.id} className="help-card history-page-row">
                <div className="history-page-main">
                  <div>
                    <p className="section-kicker">Ticker</p>
                    <h2>{run.ticker}</h2>
                  </div>
                  <div className="history-page-pills">
                    <Pill label="Signal" value={run.signal || "PENDING"} />
                    <Pill label="Execution" value={run.execution_status || "PENDING"} />
                  </div>
                </div>
                <p className="history-page-summary">{run.summary || `${run.scanner_mode} workflow`}</p>
                <div className="history-page-meta">
                  <span>{`Mode: ${run.scanner_mode}`}</span>
                  <span>{`Confidence: ${run.strategy_confidence || "--"}`}</span>
                  <span>{`Saved: ${shortDateTime(run.created_at)}`}</span>
                </div>
              </article>
            ))
          ) : (
            <article className="help-card">
              <h2>No saved runs yet</h2>
              <p>Run the workspace while signed in to start building your execution and decision history.</p>
            </article>
          )}
        </section>
      )}
    </main>
  );
}
