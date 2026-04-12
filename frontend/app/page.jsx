"use client";

import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import Link from "next/link";

const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";
const TOKEN_STORAGE_KEY = "trading_platform_access_token";
const USER_STORAGE_KEY = "trading_platform_user";

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

function humanizeFetchError(error) {
  const message = String(error?.message || error || "");
  if (message.toLowerCase() === "failed to fetch" || message.includes("NetworkError")) {
    return "Could not reach the backend API. Restart the frontend, confirm the backend is running on port 8000, and try both localhost:3000 and 127.0.0.1:3000.";
  }
  return message || "Request failed";
}

function money(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(numeric)
    : "--";
}

function number(value, digits = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? new Intl.NumberFormat("en-US", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      }).format(numeric)
    : "--";
}

function percent(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${Math.round(numeric * 100)}%` : "--";
}

function statusClass(value) {
  const normalized = String(value || "").toLowerCase();
  if (["buy", "approved", "submitted"].includes(normalized)) return normalized;
  if (["sell", "rejected", "failed"].includes(normalized)) return normalized;
  if (["hold", "skipped", "pending"].includes(normalized)) return normalized;
  return "neutral";
}

function Pill({ label, value }) {
  return (
    <span className={`pill ${statusClass(value)}`} key={`${label}-${value}`}>
      {`${label}: ${value}`}
    </span>
  );
}

function TopStat({ label, value, tone = "neutral" }) {
  return (
    <div className={`top-stat ${tone}`}>
      <span>{label}</span>
      <strong key={`${label}-${value}`}>{value}</strong>
    </div>
  );
}

function shortDate(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(date);
}

function useCountUp(value, duration = 700) {
  const [displayValue, setDisplayValue] = useState(() => Number(value) || 0);

  useEffect(() => {
    const target = Number(value);
    if (!Number.isFinite(target)) {
      return;
    }

    let frameId = 0;
    const startValue = displayValue;
    const startTime = performance.now();

    function tick(now) {
      const progress = Math.min((now - startTime) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      const nextValue = startValue + (target - startValue) * eased;
      setDisplayValue(nextValue);
      if (progress < 1) {
        frameId = window.requestAnimationFrame(tick);
      }
    }

    frameId = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frameId);
  }, [value]);

  return displayValue;
}

function AnimatedMetricValue({ value, formatter = (input) => input, className = "" }) {
  const numericValue = Number(value);
  const animated = useCountUp(Number.isFinite(numericValue) ? numericValue : 0);

  if (!Number.isFinite(numericValue)) {
    return <h3 className={className}>--</h3>;
  }

  return <h3 className={className}>{formatter(animated)}</h3>;
}

function SkeletonText({ width = "100%" }) {
  return <div className="skeleton-line" style={{ width }} />;
}

function PriceChart({ marketData, signal, loading }) {
  const bars = marketData?.recent_bars || [];
  const hasBars = bars.length > 0;
  const highs = hasBars ? bars.map((bar) => Number(bar.high)) : [];
  const lows = hasBars ? bars.map((bar) => Number(bar.low)) : [];
  const minLow = hasBars ? Math.min(...lows) : 0;
  const maxHigh = hasBars ? Math.max(...highs) : 1;
  const range = Math.max(maxHigh - minLow, 1);
  const chartHeight = 208;
  const barWidth = 8;
  const gap = 5;
  const width = Math.max(bars.length, 24) * (barWidth + gap);
  const yFor = (price) => chartHeight - ((price - minLow) / range) * (chartHeight - 14) - 7;

  return (
    <div className="chart-shell">
      <div className="chart-head">
        <div>
          <p className="section-kicker">Price Action</p>
          <h3>{marketData?.ticker || "Selected Chart"}</h3>
        </div>
        <div className="chart-meta">
          <Pill label="Signal" value={signal || "PENDING"} />
          <span>{shortDate(marketData?.latest_close_date)}</span>
        </div>
      </div>
      <div className={`chart-viewport ${loading ? "is-loading" : ""}`}>
        {loading ? (
          <div className="chart-skeleton-grid">
            <SkeletonText width="42%" />
            <SkeletonText width="78%" />
            <SkeletonText width="64%" />
          </div>
        ) : hasBars ? (
          <svg className="price-chart" viewBox={`0 0 ${width} ${chartHeight}`} preserveAspectRatio="none" role="img" aria-label="Recent price chart">
            {[0.2, 0.4, 0.6, 0.8].map((line) => (
              <line
                key={line}
                x1="0"
                x2={width}
                y1={chartHeight * line}
                y2={chartHeight * line}
                className="chart-grid-line"
              />
            ))}
            {bars.map((bar, index) => {
              const x = index * (barWidth + gap) + 2;
              const openY = yFor(Number(bar.open));
              const closeY = yFor(Number(bar.close));
              const highY = yFor(Number(bar.high));
              const lowY = yFor(Number(bar.low));
              const candleTop = Math.min(openY, closeY);
              const candleHeight = Math.max(Math.abs(openY - closeY), 2);
              const tone = Number(bar.close) >= Number(bar.open) ? "up" : "down";

              return (
                <g key={`${bar.date}-${index}`} className={`chart-candle ${tone}`}>
                  <line x1={x + barWidth / 2} x2={x + barWidth / 2} y1={highY} y2={lowY} className="chart-wick" />
                  <rect x={x} y={candleTop} width={barWidth} height={candleHeight} rx="2" className="chart-body" />
                </g>
              );
            })}
            <line x1="0" x2={width} y1={yFor(Number(marketData?.sma_50 || 0))} y2={yFor(Number(marketData?.sma_50 || 0))} className="chart-ma chart-ma-fast" />
            <line x1="0" x2={width} y1={yFor(Number(marketData?.sma_200 || 0))} y2={yFor(Number(marketData?.sma_200 || 0))} className="chart-ma chart-ma-slow" />
          </svg>
        ) : (
          <div className="chart-empty">Run a workflow to load recent market history.</div>
        )}
      </div>
      <div className="chart-legend">
        <span><i className="legend-swatch fast" /> SMA 50</span>
        <span><i className="legend-swatch slow" /> SMA 200</span>
        <span><i className="legend-swatch candle" /> 24D candles</span>
      </div>
    </div>
  );
}

function StatCard({ label, loading, children }) {
  return (
    <article className="metric card">
      <p className="metric-label">{label}</p>
      {loading ? (
        <div className="metric-skeleton">
          <SkeletonText width="50%" />
          <SkeletonText width="72%" />
        </div>
      ) : (
        children
      )}
    </article>
  );
}

function Timeline({ state }) {
  if (!state) {
    return (
      <div className="timeline empty-state">
        Run a workflow to load activity.
      </div>
    );
  }

  const items = [
    {
      title: "Market Data",
      copy: `Latest close ${state.market_data?.latest_close_date || "--"} with current price ${money(
        state.market_data?.current_price
      )}.`,
    },
    { title: "Strategy", copy: state.strategy_reason || "No strategy rationale returned." },
    { title: "Risk", copy: state.risk_reason || "No risk rationale returned." },
    { title: "Execution", copy: state.execution_detail || "Execution not attempted." },
  ];

  return (
    <div className="timeline">
      {items.map((item, index) => (
        <div className="timeline-item" key={item.title} style={{ animationDelay: `${index * 80}ms` }}>
          <h4>{item.title}</h4>
          <p>{item.copy}</p>
        </div>
      ))}
    </div>
  );
}

function CommandPalette({ open, onClose, commands }) {
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (!open) {
      setQuery("");
    }
  }, [open]);

  if (!open) return null;

  const filtered = commands.filter((command) => {
    const haystack = `${command.label} ${command.description || ""}`.toLowerCase();
    return haystack.includes(query.trim().toLowerCase());
  });

  return (
    <div className="command-overlay" onClick={onClose}>
      <div className="command-palette" onClick={(event) => event.stopPropagation()}>
        <div className="command-input-row">
          <span className="command-kbd">⌘K</span>
          <input
            autoFocus
            type="text"
            value={query}
            placeholder="Search actions, navigation, and workspace tools"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") onClose();
            }}
          />
        </div>
        <div className="command-results">
          {filtered.length ? (
            filtered.map((command) => (
              <button
                key={command.label}
                type="button"
                className="command-item"
                onClick={() => {
                  command.onSelect();
                  onClose();
                }}
              >
                <strong>{command.label}</strong>
                <span>{command.description}</span>
              </button>
            ))
          ) : (
            <div className="command-empty">No matching actions.</div>
          )}
        </div>
      </div>
    </div>
  );
}

function RunHistoryPanel({ history }) {
  return (
    <article className="card history-card">
      <div className="card-head">
        <div>
          <p className="section-kicker">History</p>
          <h3>Recent Runs</h3>
        </div>
      </div>
      <div className="history-list">
        {history.length ? (
          history.map((run) => (
            <div className="history-row" key={run.id}>
              <div>
                <strong>{run.ticker}</strong>
                <span>{run.summary || `${run.scanner_mode} workflow`}</span>
              </div>
              <div className="history-meta">
                <Pill label="Signal" value={run.signal || "PENDING"} />
                <span>{shortDate(run.created_at)}</span>
              </div>
            </div>
          ))
        ) : (
          <div className="empty-state">No saved runs yet.</div>
        )}
      </div>
    </article>
  );
}

function AuthPanel({
  authMode,
  setAuthMode,
  authForm,
  setAuthForm,
  authPending,
  authError,
  onSubmit,
  googleRef,
  googleEnabled,
  googleReady,
  googleError,
}) {
  return (
    <section className="auth-panel-compact">
      <p className="section-kicker">Optional Sign In</p>
      <h3>Save your workspace access.</h3>
      <p className="auth-copy">
        Sign in to connect providers and enable execution.
      </p>

      <div className="auth-tabs">
        <button
          type="button"
          className={authMode === "login" ? "is-active" : ""}
          onClick={() => setAuthMode("login")}
        >
          Login
        </button>
        <button
          type="button"
          className={authMode === "register" ? "is-active" : ""}
          onClick={() => setAuthMode("register")}
        >
          Register
        </button>
      </div>

      <form
        className="auth-form"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        {authMode === "register" ? (
          <input
            type="text"
            placeholder="Full name"
            value={authForm.fullName}
            onChange={(event) => setAuthForm((current) => ({ ...current, fullName: event.target.value }))}
          />
        ) : null}
        <input
          type="email"
          placeholder="Email"
          value={authForm.email}
          onChange={(event) => setAuthForm((current) => ({ ...current, email: event.target.value }))}
        />
        <input
          type="password"
          placeholder="Password"
          value={authForm.password}
          onChange={(event) => setAuthForm((current) => ({ ...current, password: event.target.value }))}
        />
        {authError ? <div className="auth-error">{authError}</div> : null}
        <button type="submit" className="auth-submit" disabled={authPending}>
          {authPending ? "Please wait..." : authMode === "login" ? "Login" : "Create account"}
        </button>
      </form>

      {googleEnabled && googleError ? (
        <div className="google-auth-block">
          <div className="auth-divider">Google sign-in unavailable</div>
          <div className="auth-helper">{googleError}</div>
        </div>
      ) : googleEnabled ? (
        <div className="google-auth-block">
          <div className="auth-divider">or continue with Google</div>
          <div ref={googleRef} className="google-signin-slot" />
          {!googleReady ? <div className="auth-helper">Loading Google sign-in…</div> : null}
        </div>
      ) : (
        <div className="google-auth-block">
          <div className="auth-divider">Google sign-in unavailable</div>
          <div className="auth-helper">Set `NEXT_PUBLIC_GOOGLE_CLIENT_ID` in the frontend environment to enable it.</div>
        </div>
      )}
    </section>
  );
}

function SignedInPanel({ user, onSignOut }) {
  return (
    <section className="sidebar-panel user-panel">
      <p className="section-kicker">Workspace</p>
      <div className="user-row">
        <div className="user-avatar">{(user.full_name || user.email).slice(0, 1).toUpperCase()}</div>
        <div>
          <strong>{user.full_name || user.email}</strong>
          <div className="user-meta">{user.email}</div>
        </div>
      </div>
      <button type="button" className="secondary-button" onClick={onSignOut}>
        Sign out
      </button>
    </section>
  );
}

function GuestPanel(props) {
  const { onOpenAuth } = props;
  return (
    <section className="sidebar-panel">
      <p className="section-kicker">Access</p>
      <h3 className="guest-title">Sign in only when you want to execute.</h3>
      <button type="button" className="secondary-button guest-cta" onClick={onOpenAuth}>
        Login To Trade
      </button>
      <Link href="/help" className="help-inline-link">
        View Help
      </Link>
    </section>
  );
}

function ProviderConnectionsPanel({
  integrationForm,
  setIntegrationForm,
  integrationPending,
  integrationError,
  connectedIntegrations,
  onConnect,
  onDisconnect,
}) {
  const providerMeta =
    connectedIntegrations.find((connection) => connection.provider === "alpaca") || {
      provider: "alpaca",
      display_name: "Alpaca",
      auth_fields: ["api_key", "secret_key"],
      supports_execution: true,
    };
  const selectedConnection =
    connectedIntegrations.find((connection) => connection.provider === "alpaca") || null;
  const needsSecret = providerMeta?.auth_fields?.includes("secret_key");

  return (
    <section className="sidebar-panel">
      <p className="section-kicker">Connections</p>
      <div className={`health-banner ${selectedConnection ? "is-ok" : "is-error"}`}>
        {selectedConnection
          ? `${selectedConnection.display_name} connected.`
          : providerMeta?.supports_execution
            ? "No execution-capable provider connected. Runs stay in analysis mode until the user connects one."
            : "Connect optional tools to personalize data, AI, and observability over time."}
      </div>
      {selectedConnection ? (
        <button type="button" className="secondary-button" onClick={onDisconnect} disabled={integrationPending}>
          Disconnect Alpaca
        </button>
      ) : (
        <form
          className="auth-form broker-form"
          onSubmit={(event) => {
            event.preventDefault();
            onConnect();
          }}
        >
          <div className="provider-lockup">Alpaca Paper Trading</div>
          <input
            type="text"
            placeholder="Connection label (optional)"
            value={integrationForm.label}
            onChange={(event) => setIntegrationForm((current) => ({ ...current, label: event.target.value }))}
          />
          <input
            type="text"
            placeholder={`${providerMeta?.display_name || "Provider"} API key`}
            value={integrationForm.apiKey}
            onChange={(event) => setIntegrationForm((current) => ({ ...current, apiKey: event.target.value }))}
          />
          {needsSecret ? (
            <input
              type="password"
              placeholder={`${providerMeta?.display_name || "Provider"} secret key`}
              value={integrationForm.secretKey}
              onChange={(event) => setIntegrationForm((current) => ({ ...current, secretKey: event.target.value }))}
            />
          ) : null}
          {integrationError ? <div className="auth-error">{integrationError}</div> : null}
          <button type="submit" className="auth-submit" disabled={integrationPending || !providerMeta}>
            {integrationPending ? "Saving..." : `Connect ${providerMeta?.display_name}`}
          </button>
        </form>
      )}
    </section>
  );
}

export default function Page() {
  const apiBaseUrl = useMemo(
    () => (process.env.NEXT_PUBLIC_API_BASE_URL || DEFAULT_API_BASE_URL).replace(/\/$/, ""),
    []
  );
  const apiBaseCandidates = useMemo(() => getApiBaseCandidates(apiBaseUrl), [apiBaseUrl]);
  const googleClientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || "";
  const googleButtonRef = useRef(null);

  const [ticker, setTicker] = useState("");
  const [health, setHealth] = useState("Checking platform health...");
  const [healthError, setHealthError] = useState(false);
  const [workflowState, setWorkflowState] = useState(null);
  const [runPending, setRunPending] = useState(false);
  const [copilotPending, setCopilotPending] = useState(false);
  const [copilotInput, setCopilotInput] = useState("");
  const [copilotModel, setCopilotModel] = useState("--");
  const [toast, setToast] = useState("");
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content:
        "Ask me to explain runs, compare history, scan the market, call platform tools, or execute a trade when your broker is connected.",
    },
  ]);
  const [token, setToken] = useState(null);
  const [user, setUser] = useState(null);
  const [authMode, setAuthMode] = useState("login");
  const [authPending, setAuthPending] = useState(false);
  const [authError, setAuthError] = useState("");
  const [authForm, setAuthForm] = useState({ email: "", password: "", fullName: "" });
  const [providers, setProviders] = useState([]);
  const [integrationForm, setIntegrationForm] = useState({ label: "", apiKey: "", secretKey: "" });
  const [connectedIntegrations, setConnectedIntegrations] = useState([]);
  const [integrationPending, setIntegrationPending] = useState(false);
  const [integrationError, setIntegrationError] = useState("");
  const [showAuthPanel, setShowAuthPanel] = useState(false);
  const [copilotMinimized, setCopilotMinimized] = useState(true);
  const [googleReady, setGoogleReady] = useState(false);
  const [googleError, setGoogleError] = useState("");
  const [googleScriptReady, setGoogleScriptReady] = useState(false);
  const [showCommandPalette, setShowCommandPalette] = useState(false);
  const [runHistory, setRunHistory] = useState([]);
  const [isRunTransitionPending, startRunTransition] = useTransition();

  useEffect(() => {
    const storedToken = window.localStorage.getItem(TOKEN_STORAGE_KEY);
    const storedUser = window.localStorage.getItem(USER_STORAGE_KEY);
    if (storedToken) {
      setToken(storedToken);
    }
    if (storedUser) {
      try {
        setUser(JSON.parse(storedUser));
      } catch {
        window.localStorage.removeItem(USER_STORAGE_KEY);
      }
    }
  }, []);

  useEffect(() => {
    async function loadHealth() {
      try {
        const response = await fetchWithFallback("/api/health");
        if (!response.ok) throw new Error("health request failed");
        const payload = await response.json();
        setHealth(`${payload.platform} is online.`);
        setHealthError(false);
      } catch (error) {
        setHealth(humanizeFetchError(error) || "Platform health check failed.");
        setHealthError(true);
      }
    }

    loadHealth();
  }, [apiBaseUrl]);

  useEffect(() => {
    async function loadProviders() {
      try {
        const response = await fetchWithFallback("/api/integrations/providers");
        if (!response.ok) throw new Error("providers request failed");
        const payload = await response.json();
        setProviders(payload);
      } catch {
        setProviders([]);
      }
    }

    loadProviders();
  }, [apiBaseUrl]);

  async function fetchWithFallback(path, options = {}) {
    let lastError = null;

    for (const baseUrl of apiBaseCandidates) {
      try {
        return await fetch(`${baseUrl}${path}`, options);
      } catch (error) {
        lastError = error;
      }
    }

    throw lastError || new Error("Failed to fetch");
  }

  useEffect(() => {
    if (!googleClientId) {
      setGoogleScriptReady(false);
      setGoogleError("");
      return;
    }

    if (window.google?.accounts?.id) {
      setGoogleScriptReady(true);
      setGoogleError("");
      return;
    }

    const existingScript = document.getElementById("google-gsi-script");
    if (existingScript) {
      const intervalId = window.setInterval(() => {
        if (window.google?.accounts?.id) {
          setGoogleScriptReady(true);
          setGoogleError("");
          window.clearInterval(intervalId);
        }
      }, 150);
      const timeoutId = window.setTimeout(() => {
        if (!window.google?.accounts?.id) {
          setGoogleError("Google sign-in could not be loaded. Check browser extensions, network access, or allowed Google OAuth origins.");
        }
        window.clearInterval(intervalId);
      }, 5000);
      return () => {
        window.clearTimeout(timeoutId);
        window.clearInterval(intervalId);
      };
    }

    const script = document.createElement("script");
    script.id = "google-gsi-script";
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.defer = true;
    script.onload = () => {
      setGoogleScriptReady(true);
      setGoogleError("");
    };
    script.onerror = () => {
      setGoogleError("Google sign-in script failed to load. Check browser extensions, network access, or allowed Google OAuth origins.");
      setGoogleScriptReady(false);
    };
    document.head.appendChild(script);

    const timeoutId = window.setTimeout(() => {
      if (!window.google?.accounts?.id) {
        setGoogleError("Google sign-in could not be loaded. Check browser extensions, network access, or allowed Google OAuth origins.");
      }
    }, 5000);

    return () => window.clearTimeout(timeoutId);
  }, [googleClientId]);

  useEffect(() => {
    if (!googleClientId || token) {
      setGoogleReady(false);
      return;
    }

    let cancelled = false;

    function tryRenderGoogleButton() {
      if (cancelled) return;
      if (!window.google?.accounts?.id || !googleButtonRef.current) {
        window.setTimeout(tryRenderGoogleButton, 150);
        return;
      }

      googleButtonRef.current.innerHTML = "";

      setGoogleReady(true);

      const handleGoogleCredential = async (response) => {
        setAuthPending(true);
        setAuthError("");
        try {
          const result = await fetchWithFallback("/api/auth/google", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ credential: response.credential }),
          });
          if (!result.ok) {
            throw new Error((await result.text()) || "Google authentication failed");
          }
          const payload = await result.json();
          persistSession(payload.access_token, payload.user);
        } catch (error) {
          setAuthError(error.message);
        } finally {
          setAuthPending(false);
        }
      };

      window.google.accounts.id.initialize({
        client_id: googleClientId,
        callback: handleGoogleCredential,
      });
      window.google.accounts.id.renderButton(googleButtonRef.current, {
        theme: "outline",
        size: "large",
        width: "320",
        text: "continue_with",
      });
    }

    if (showAuthPanel) {
      tryRenderGoogleButton();
    }

    return () => {
      cancelled = true;
    };
  }, [apiBaseUrl, googleClientId, token, showAuthPanel, apiBaseCandidates, googleScriptReady]);

  useEffect(() => {
    async function loadIntegrations() {
      if (!token) {
        setConnectedIntegrations([]);
        return;
      }

      try {
        const response = await apiFetch("/api/integrations");
        if (!response.ok) {
          throw new Error((await response.text()) || "Could not load integrations");
        }
        const payload = await response.json();
        setConnectedIntegrations(payload);
      } catch (error) {
        setIntegrationError(humanizeFetchError(error));
      }
    }

    loadIntegrations();
  }, [token]);

  useEffect(() => {
    async function loadHistory() {
      if (!token) {
        setRunHistory([]);
        return;
      }

      try {
        const response = await apiFetch("/api/history");
        if (!response.ok) {
          throw new Error((await response.text()) || "Could not load history");
        }
        const payload = await response.json();
        setRunHistory(payload);
      } catch {
        setRunHistory([]);
      }
    }

    loadHistory();
  }, [token]);

  useEffect(() => {
    function handleKeydown(event) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setShowCommandPalette((current) => !current);
      }
      if (event.key === "Escape") {
        setShowCommandPalette(false);
      }
    }

    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, []);

  function persistSession(accessToken, currentUser) {
    setToken(accessToken);
    setUser(currentUser);
    setShowAuthPanel(false);
    window.localStorage.setItem(TOKEN_STORAGE_KEY, accessToken);
    window.localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(currentUser));
    setAuthError("");
  }

  function clearSession() {
    setToken(null);
    setUser(null);
    setWorkflowState(null);
    setConnectedIntegrations([]);
    window.localStorage.removeItem(TOKEN_STORAGE_KEY);
    window.localStorage.removeItem(USER_STORAGE_KEY);
  }

  function pushToast(message) {
    setToast(message);
    window.clearTimeout(pushToast.timeoutId);
    pushToast.timeoutId = window.setTimeout(() => setToast(""), 3200);
  }

  async function submitAuthForm() {
    setAuthPending(true);
    setAuthError("");
    try {
      const endpoint = authMode === "login" ? "/api/auth/login" : "/api/auth/register";
      const payload =
        authMode === "login"
          ? { email: authForm.email, password: authForm.password }
          : { email: authForm.email, password: authForm.password, full_name: authForm.fullName };
      const response = await fetchWithFallback(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error((await response.text()) || "Authentication failed");
      }
      const result = await response.json();
      persistSession(result.access_token, result.user);
      setIntegrationError("");
    } catch (error) {
      setAuthError(humanizeFetchError(error));
    } finally {
      setAuthPending(false);
    }
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

    if (response.status === 401 && token) {
      clearSession();
      throw new Error("Session expired. Please sign in again.");
    }
    return response;
  }

  async function runPlatform(confirmExecution = false) {
    const symbol = ticker.trim().toUpperCase();
    const activeTicker = confirmExecution ? (workflowState?.ticker || symbol) : symbol;
    setRunPending(true);
    try {
      const payload = {
        ...(activeTicker ? { ticker: activeTicker } : {}),
        ...(confirmExecution ? { confirm_execution: true } : {}),
      };
      const response = await apiFetch("/api/run", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error((await response.text()) || "workflow run failed");
      }
      const state = await response.json();
      setWorkflowState(state);
      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          content:
            state.scanner_mode === "manual"
              ? `Ran the manual override for ${state.ticker}. You can ask why the trade was approved, rejected, or what to do next.`
              : `Scanned the market and selected ${state.ticker}. You can ask why this candidate won or what the next step should be.`,
        },
      ]);
      pushToast(
        confirmExecution && state.execution_status === "SUBMITTED"
          ? `Execution submitted for ${state.ticker}.`
          : state.scanner_mode === "manual"
          ? `Completed workflow for ${state.ticker}.`
          : `Completed autonomous scan and selected ${state.ticker}.`
      );
      if (token) {
        const historyResponse = await apiFetch("/api/history");
        if (historyResponse.ok) {
          const historyPayload = await historyResponse.json();
          setRunHistory(historyPayload);
        }
      }
    } catch (error) {
      pushToast(`Platform run failed: ${humanizeFetchError(error)}`);
    } finally {
      setRunPending(false);
    }
  }

  function handleRunSubmit(event, confirmExecution = false) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    startRunTransition(() => {
      void runPlatform(confirmExecution);
    });
  }

  async function connectProvider() {
    if (!token) {
      pushToast("Sign in first to connect your own providers.");
      return;
    }

    const providerMeta = providers.find((provider) => provider.provider === "alpaca");
    setIntegrationPending(true);
    setIntegrationError("");
    try {
      const response = await apiFetch("/api/integrations/alpaca", {
        method: "POST",
        body: JSON.stringify({
          provider: "alpaca",
          environment: providerMeta?.default_environment || "production",
          label: integrationForm.label || null,
          api_key: integrationForm.apiKey,
          secret_key: integrationForm.secretKey || null,
        }),
      });
      if (!response.ok) {
        throw new Error((await response.text()) || "Could not connect provider");
      }
      const payload = await response.json();
      setConnectedIntegrations((current) => {
        const withoutSelected = current.filter((connection) => connection.provider !== payload.provider);
        return [...withoutSelected, payload];
      });
      setIntegrationForm((current) => ({ ...current, secretKey: "" }));
      pushToast(`Connected ${payload.display_name}.`);
    } catch (error) {
      const message = humanizeFetchError(error);
      setIntegrationError(message);
      pushToast(message);
    } finally {
      setIntegrationPending(false);
    }
  }

  async function disconnectProvider() {
    setIntegrationPending(true);
    setIntegrationError("");
    try {
      const response = await apiFetch("/api/integrations/alpaca", { method: "DELETE" });
      if (!response.ok) {
        throw new Error((await response.text()) || "Could not disconnect provider");
      }
      setConnectedIntegrations((current) =>
        current.filter((connection) => connection.provider !== "alpaca")
      );
      setIntegrationForm({ label: "", apiKey: "", secretKey: "" });
      pushToast("Disconnected alpaca.");
    } catch (error) {
      const message = humanizeFetchError(error);
      setIntegrationError(message);
      pushToast(message);
    } finally {
      setIntegrationPending(false);
    }
  }

  async function askCopilot() {
    const message = copilotInput.trim();
    if (!message) {
      pushToast("Enter a copilot question first.");
      return;
    }

    setMessages((current) => [...current, { role: "user", content: message }]);
    setCopilotInput("");
    setCopilotPending(true);

    try {
      const response = await apiFetch("/api/copilot", {
        method: "POST",
        body: JSON.stringify({ message }),
      });
      if (!response.ok) {
        throw new Error((await response.text()) || "copilot request failed");
      }
      const payload = await response.json();
      setCopilotModel(payload.model || "--");
      setMessages((current) => [...current, { role: "assistant", content: payload.reply }]);
    } catch (error) {
      setMessages((current) => [
        ...current,
        { role: "assistant", content: `I could not answer that just now: ${error.message}` },
      ]);
    } finally {
      setCopilotPending(false);
    }
  }

  const market = workflowState?.market_data || {};
  const risk = workflowState?.risk_details || {};
  const signal = workflowState?.signal || "PENDING";
  const riskStatus = workflowState ? (workflowState?.risk_approved ? "APPROVED" : "REJECTED") : "PENDING";
  const execution = workflowState?.execution_status || "PENDING";
  const awaitingExecutionConfirmation = workflowState?.execution_status === "AWAITING_CONFIRMATION";
  const scanCandidates = workflowState?.scan_candidates || [];
  const executionConnection = connectedIntegrations.find((connection) => connection.supports_execution);
  const strategyConfidence = percent(workflowState?.strategy_confidence);
  const riskControlsCount = (workflowState?.risk_controls_triggered || []).length;
  const selectedMode = workflowState?.scanner_mode === "manual" ? "Manual Override" : "Autonomous Scan";
  const showLoadingSkeletons = runPending && !workflowState;
  const commands = [
    {
      label: "Run workspace",
      description: "Start autonomous scan and evaluation",
      onSelect: () => runPlatform(),
    },
    {
      label: "Focus ticker override",
      description: "Jump to the manual ticker input",
      onSelect: () => document.getElementById("ticker-input")?.focus(),
    },
    {
      label: "Open help",
      description: "View documentation and product guidance",
      onSelect: () => {
        window.location.href = "/help";
      },
    },
    ...(token
      ? [
          {
            label: "Open history",
            description: "Review saved workflow runs",
            onSelect: () => {
              window.location.href = "/history";
            },
          },
        ]
      : []),
    token
      ? {
          label: "Sign out",
          description: "End the current session",
          onSelect: () => clearSession(),
        }
      : {
          label: "Open sign in",
          description: "Authenticate to connect Alpaca and trade",
          onSelect: () => setShowAuthPanel(true),
        },
  ];

  return (
    <>
      <CommandPalette open={showCommandPalette} onClose={() => setShowCommandPalette(false)} commands={commands} />
      <div className="shell">
        <aside className="sidebar">
          <div className="brand">
            <img src="/brand-mark.svg" alt="Platform logo" className="brand-mark" />
            <div>
              <p className="eyebrow">Platform</p>
              <h1>Multi Agent Equity Trading Platform</h1>
            </div>
          </div>

          {token && user ? (
            <SignedInPanel user={user} onSignOut={clearSession} />
          ) : (
            <GuestPanel
              authMode={authMode}
              setAuthMode={setAuthMode}
              authForm={authForm}
              setAuthForm={setAuthForm}
              authPending={authPending}
              authError={authError}
              onSubmit={submitAuthForm}
              googleRef={googleButtonRef}
              googleEnabled={Boolean(googleClientId)}
              googleError={googleError}
              onOpenAuth={() => setShowAuthPanel(true)}
            />
          )}

          {token && user ? (
            <ProviderConnectionsPanel
              integrationForm={integrationForm}
              setIntegrationForm={setIntegrationForm}
              integrationPending={integrationPending}
              integrationError={integrationError}
              connectedIntegrations={connectedIntegrations}
              onConnect={connectProvider}
              onDisconnect={disconnectProvider}
            />
          ) : null}

          <section className="sidebar-panel">
            <p className="section-kicker">Pipeline</p>
            <div className="workflow-rail">
              <span>Market Intelligence</span>
              <span>Strategy Engine</span>
              <span>Risk Engine</span>
              <span>Execution</span>
            </div>
          </section>

          <section className="sidebar-panel">
            <label className="input-label" htmlFor="ticker-input">
              Manual Override
            </label>
            <form
              className="input-row"
              onSubmit={(event) => handleRunSubmit(event)}
            >
              <input
                id="ticker-input"
                type="text"
                value={ticker}
                maxLength={10}
                autoComplete="off"
                placeholder="Optional ticker override"
                onChange={(event) => setTicker(event.target.value)}
              />
              <button type="submit" disabled={runPending || isRunTransitionPending}>
                {runPending || isRunTransitionPending ? "Running..." : "Run"}
              </button>
            </form>
            <p className="input-help">Leave blank to auto-select a candidate.</p>
            <button type="button" className="command-trigger" onClick={() => setShowCommandPalette(true)}>
              Open Command Palette
              <span>Ctrl/⌘ K</span>
            </button>
          </section>

          <section className="sidebar-panel">
            <p className="section-kicker">System Status</p>
            <div className={`health-banner ${healthError ? "is-error" : "is-ok"}`}>{health}</div>
          </section>

          <div className="sidebar-footer">
            <div className="profile-badge">{token && user ? (user.full_name || user.email).slice(0, 1).toUpperCase() : "G"}</div>
          </div>
        </aside>

        <main className="content">
          <section className="topbar">
            <div className="topbar-copy">
              <p className="eyebrow">Workspace</p>
              <h2 className="topbar-title">Autonomous equity workflows with integrated oversight.</h2>
            </div>
            <div className="topbar-actions">
              {token ? (
                <Link href="/history" className="help-link">
                  History
                </Link>
              ) : null}
              <Link href="/help" className="help-link">
                Help
              </Link>
              <div className="topbar-stats">
              <TopStat label="Mode" value={workflowState ? selectedMode : "Standby"} />
              <TopStat label="Confidence" value={workflowState ? strategyConfidence : "--"} tone="accent" />
              <TopStat label="Controls" value={workflowState ? String(riskControlsCount) : "--"} />
              <TopStat
                label="Execution"
                value={execution}
                tone={execution.toLowerCase() === "submitted" ? "good" : execution.toLowerCase() === "failed" ? "bad" : "neutral"}
              />
              </div>
            </div>
          </section>

          <section className="workspace-banner card">
            <div>
              <p className="section-kicker">Overview</p>
              <h3>Scan, evaluate, risk-check, and route from a single workspace.</h3>
            </div>
            <div className="banner-meta">
              {showLoadingSkeletons ? (
                <div className="banner-skeletons">
                  <SkeletonText width="110px" />
                  <SkeletonText width="88px" />
                </div>
              ) : (
                <>
                  <span>{workflowState?.ticker || "No active selection"}</span>
                  <span>{workflowState?.scanner_mode === "manual" ? "Manual override" : "Auto selection"}</span>
                </>
              )}
            </div>
          </section>

          <section className="grid analytics-grid analytics-grid-primary">
            <article className="metric card selection-card">
              <p className="metric-label">Selected Candidate</p>
              {showLoadingSkeletons ? (
                <div className="metric-skeleton">
                  <SkeletonText width="58%" />
                  <SkeletonText width="76%" />
                </div>
              ) : (
                <>
                  <AnimatedMetricValue value={market.current_price} formatter={(input) => money(input)} />
                  <p className="metric-subtle">
                    {workflowState
                      ? `${workflowState.ticker || "--"} | ${workflowState.scanner_mode === "manual" ? "manual override" : "auto-selected"}`
                      : "Run to load state."}
                  </p>
                </>
              )}
            </article>
            <article className="card chart-card">
              <PriceChart marketData={market} signal={signal} loading={showLoadingSkeletons} />
            </article>
          </section>

          <section className="grid metrics-grid metrics-grid-secondary">
            <StatCard label="Signal" loading={showLoadingSkeletons}>
              <div className="metric-pill-row">
                <Pill label="Signal" value={signal} />
              </div>
              <p className="metric-subtle">
                {workflowState
                  ? `Confidence ${percent(workflowState?.strategy_confidence)}`
                  : "No signal yet."}
              </p>
            </StatCard>

            <StatCard label="Risk Gate" loading={showLoadingSkeletons}>
              <div className="metric-pill-row">
                <Pill label="Risk" value={riskStatus} />
              </div>
              <p className="metric-subtle">
                Shares {workflowState?.share_count ?? 0} | Controls {(workflowState?.risk_controls_triggered || []).length}
              </p>
            </StatCard>

            <StatCard label="Execution" loading={showLoadingSkeletons}>
              <div className="metric-pill-row">
                <Pill label="Execution" value={execution} />
              </div>
              <p className="metric-subtle">
                {workflowState?.execution_detail ||
                  (executionConnection
                    ? "Connected execution path available."
                    : "Execution requires a connected provider.")}
              </p>
              {awaitingExecutionConfirmation ? (
                <button
                  type="button"
                  className="execution-confirm-button"
                  onClick={(event) => handleRunSubmit(event, true)}
                  disabled={runPending || isRunTransitionPending}
                >
                  {runPending || isRunTransitionPending ? "Submitting..." : `Approve & Execute ${workflowState?.ticker || ""}`.trim()}
                </button>
              ) : null}
            </StatCard>
          </section>

          <section className="grid analytics-grid">
            <article className="card control-card">
              <div className="card-head">
                <div>
                  <p className="section-kicker">Controls</p>
                  <h3>Risk Controls</h3>
                </div>
              </div>
              <dl className="detail-list">
                <div>
                  <dt>Buying Power</dt>
                  <dd>{money(risk.buying_power)}</dd>
                </div>
                <div>
                  <dt>Max Notional</dt>
                  <dd>{money(risk.max_notional_allowed)}</dd>
                </div>
                <div>
                  <dt>Average Volume</dt>
                  <dd>{`${number(risk.avg_daily_volume)} avg / ${number(risk.min_avg_daily_volume)} min`}</dd>
                </div>
                <div>
                  <dt>Triggered Controls</dt>
                  <dd>{(workflowState?.risk_controls_triggered || []).join(", ") || "None"}</dd>
                </div>
                <div>
                  <dt>Execution Tool</dt>
                  <dd>{workflowState?.execution_tool || "Not used"}</dd>
                </div>
                <div>
                  <dt>Latest Session</dt>
                  <dd>{market.latest_close_date || "--"}</dd>
                </div>
              </dl>
            </article>
          </section>

          <section className="grid main-grid">
            <article className="card narrative-card">
              <div className="card-head">
                <div>
                  <p className="section-kicker">Activity</p>
                  <h3>Decision Trail</h3>
                </div>
                <span className="chip">{`Model: ${workflowState?.decision_model || "--"}`}</span>
              </div>
              {workflowState?.scanner_summary ? <div className="scanner-summary">{workflowState.scanner_summary}</div> : null}
              <Timeline state={workflowState} />
            </article>
          </section>

          <section className="grid lower-grid">
            <article className="card insight-card">
              <div className="card-head">
                <div>
                  <p className="section-kicker">Context</p>
                  <h3>Selection Context</h3>
                </div>
              </div>
              <div className="summary-grid">
                <div className="summary-item">
                  <span className="summary-label">Selected</span>
                  <strong>{workflowState?.ticker || "--"}</strong>
                </div>
                <div className="summary-item">
                  <span className="summary-label">Mode</span>
                  <strong>{workflowState?.scanner_mode || "--"}</strong>
                </div>
                <div className="summary-item">
                  <span className="summary-label">Candidates</span>
                  <strong>{scanCandidates.length || "--"}</strong>
                </div>
                <div className="summary-item">
                  <span className="summary-label">Shares</span>
                  <strong>{workflowState?.share_count ?? "--"}</strong>
                </div>
              </div>
              <div className="candidate-list">
                {scanCandidates.length ? (
                  scanCandidates.map((candidate) => (
                    <div className="candidate-row" key={candidate.ticker}>
                      <div>
                        <strong>{candidate.ticker}</strong>
                        <span>{candidate.summary}</span>
                      </div>
                      <span>{candidate.momentum_score?.toFixed(3)}</span>
                    </div>
                  ))
                ) : (
                  <div className="empty-state">No scan candidates yet.</div>
                )}
              </div>
            </article>

            <article className="card state-card">
              <div className="card-head">
                <div>
                  <p className="section-kicker">Trace</p>
                  <h3>Workflow State</h3>
                </div>
              </div>
              <pre className="raw-state">{workflowState ? JSON.stringify(workflowState, null, 2) : "No run yet."}</pre>
            </article>
          </section>

          {token ? (
            <section className="grid history-grid">
              <RunHistoryPanel history={runHistory} />
            </section>
          ) : null}
        </main>
      </div>

      {showAuthPanel && !token ? (
        <div className="auth-overlay" onClick={() => setShowAuthPanel(false)}>
          <div className="auth-modal" onClick={(event) => event.stopPropagation()}>
            <AuthPanel
              authMode={authMode}
              setAuthMode={setAuthMode}
              authForm={authForm}
              setAuthForm={setAuthForm}
              authPending={authPending}
              authError={authError}
              onSubmit={submitAuthForm}
              googleRef={googleButtonRef}
              googleEnabled={Boolean(googleClientId)}
              googleReady={googleReady}
              googleError={googleError}
            />
          </div>
        </div>
      ) : null}

      <aside className={`copilot-dock ${copilotMinimized ? "is-minimized" : ""}`}>
        {copilotMinimized ? (
          <button
            type="button"
            className="copilot-launcher"
            onClick={() => setCopilotMinimized(false)}
            aria-label="Open copilot"
          >
            <span className="copilot-launcher-status" />
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M8 10.5h8M8 14h5M7.2 18.5l1.7-2.1h8.4A2.7 2.7 0 0 0 20 13.7V8.8A2.8 2.8 0 0 0 17.2 6H6.8A2.8 2.8 0 0 0 4 8.8v4.9a2.7 2.7 0 0 0 2.7 2.7h.5v2.1Z"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            <span className="copilot-launcher-label">AI Copilot</span>
          </button>
        ) : (
          <>
            <div className="copilot-dock-head">
              <div>
                <p className="section-kicker">Operator Console</p>
                <h3>Workspace Chat</h3>
              </div>
              <div className="copilot-dock-actions">
                <button
                  type="button"
                  className="copilot-toggle"
                  onClick={() => setCopilotMinimized(true)}
                  aria-label="Minimize copilot"
                >
                  -
                </button>
              </div>
            </div>
            <div className="copilot-messages">
              {messages.map((message, index) => (
                <div className={`copilot-message ${message.role}`} key={`${message.role}-${index}`}>
                  {message.content}
                </div>
              ))}
            </div>
            <div className="copilot-compose">
              <textarea
                rows={4}
                value={copilotInput}
                placeholder="Scan for setups, compare my last runs, or buy AAPL if risk approves"
                onChange={(event) => setCopilotInput(event.target.value)}
                onKeyDown={(event) => {
                  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") askCopilot();
                }}
              />
              <button type="button" onClick={askCopilot} disabled={copilotPending}>
                {copilotPending ? "Thinking..." : "Ask Copilot"}
              </button>
            </div>
          </>
        )}
      </aside>

      {toast ? <div className="toast">{toast}</div> : null}
    </>
  );
}
