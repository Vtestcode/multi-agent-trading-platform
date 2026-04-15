# Multi Agent Equity Trading Platform

AI-powered multi-agent equity trading platform with autonomous market scanning, strategy and risk analysis, execution approval workflows, and a real-time operator dashboard.

Backend is handled by LangGraph orchestration, PydanticAI decision agents, market data, research, risk checks, execution, and the AI copilot.

The workflow includes a stateful coordinator agent that maintains shared workflow state, delegates work to specialist agents, records tool usage, and runs validation loops over strategy, risk, and execution outputs before the run is finalized.

The copilot includes a live reasoning stream in the UI: users can watch staged planning and action logs as the copilot translates the query, reviews context, chooses an action, reasons through the evidence, calls tools or workflows, and streams back its final response. When a trade reaches `AWAITING_CONFIRMATION`, the copilot chat also shows inline `Approve` and `Decline` buttons so users can make the execution decision directly inside the conversation.

## Local Development
clone or download

Before starting:
- Create local `.env` files for the root, `backend`, and `frontend` only as needed for your setup.
- Real API keys, OAuth secrets, broker credentials, and local database URLs should stay in local `.env` files.
- If any real credential has already been pasted into a tracked file or shared publicly, rotate it immediately.

### Backend

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Backend runs on `http://127.0.0.1:8000` in local development.

Recommended:
- Use Python 3.10+ for local development.
- Keep the backend running in its own terminal while the frontend is running.

Backend environment variables:
- `DATABASE_URL`
- `JWT_SECRET_KEY`
- `ACCESS_TOKEN_EXPIRE_MINUTES`
- `GOOGLE_CLIENT_ID`
- `BROKER_CREDENTIALS_ENCRYPTION_KEY`
- `POLYGON_API_KEY`
- `TAVILY_API_KEY`
- `OPENAI_API_KEY`
- `PYDANTIC_AI_MODEL`
- `LANGSMITH_TRACING`
- `LANGSMITH_API_KEY`
- `LANGSMITH_PROJECT`
- `AUTO_TRADE_UNIVERSE`
- `AUTO_SCAN_SAMPLE_SIZE`
- `AUTO_SCAN_CONCURRENCY`
- `EXECUTION_PROVIDER`
- `CORS_ALLOW_ORIGINS`

Example:

```env
DATABASE_URL=postgresql+psycopg://username:password@hostname:5432/database_name
JWT_SECRET_KEY=change-me-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=120
GOOGLE_CLIENT_ID=your_google_oauth_client_id.apps.googleusercontent.com
BROKER_CREDENTIALS_ENCRYPTION_KEY=replace_with_a_32_byte_secret_or_fernet_key
POLYGON_API_KEY=your_polygon_api_key
TAVILY_API_KEY=your_tavily_api_key
OPENAI_API_KEY=your_openai_api_key
PYDANTIC_AI_MODEL=openai:gpt-4.1-mini
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your_langsmith_api_key
LANGSMITH_PROJECT=multi-agent-equity-trading-platform
AUTO_TRADE_UNIVERSE=NVDA,MSFT,AAPL,AMZN,META,GOOGL,AVGO,TSLA,AMD,NFLX
AUTO_SCAN_SAMPLE_SIZE=2
AUTO_SCAN_CONCURRENCY=1
EXECUTION_PROVIDER=alpaca_rest
CORS_ALLOW_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,https://your-vercel-app.vercel.app
```

Notes:
- `TAVILY_API_KEY` is required because the research agent performs live web research on the selected ticker before strategy and risk decisions run.
- `POLYGON_API_KEY` is required for historical bars, news, and indicator/tool computations.
- If Polygon quote entitlements are unavailable on the current plan, the research layer falls back to the latest market snapshot price instead of failing the workflow.
- If Polygon rate limits autonomous scans, reduce `AUTO_SCAN_SAMPLE_SIZE` and `AUTO_SCAN_CONCURRENCY`, or use a manual ticker override.

### Frontend

The frontend is a Next.js app.

```bash
cd frontend
npm install
npm run dev
```

Set the backend URL with `NEXT_PUBLIC_API_BASE_URL`:

```env
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
NEXT_PUBLIC_GOOGLE_CLIENT_ID=your_google_oauth_client_id.apps.googleusercontent.com
```

Frontend runs on `http://localhost:3000` by default.

Typical local workflow:
1. Start the backend on `127.0.0.1:8000`.
2. Start the frontend in a separate terminal with `npm run dev`.
3. Open `http://localhost:3000`.


## LangSmith Observability (Optional)
- LangSmith tracing can be enabled entirely through backend environment variables.
- LangGraph workflow runs, API endpoints, and PydanticAI agent calls are prepared for LangSmith tracing.
- Set `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, and optionally `LANGSMITH_PROJECT` on Heroku.
- Backend health responses now report whether tracing is enabled.

## Platform Design
- The default behavior is autonomous: scan a configured equity universe, pick the strongest momentum candidate, and run the trading workflow on that selection.
- Autonomous scans use a small subset from the default universe on each run to reduce Polygon request pressure, and the sample size/concurrency can be tuned with environment variables.
- The coordinator agent maintains shared state, tracks delegation steps, and stores a tool call history for every workflow run.
- The workflow now includes a dedicated research step after market data and before strategy generation.
- Strategy, risk, and execution each pass through bounded validation loops so malformed outputs are normalized before the workflow continues.
- Signing in is optional for public users. Guests can scan, analyze, and use the copilot.
- Users authenticate with JWT-backed sessions, with local email/password auth and Google sign-in support.
- User records are stored in Postgres, which maps directly to Heroku Postgres for deployment.
- Provider credentials are not shared from backend environment variables. Signed-in users connect their own providers, and those credentials are stored encrypted in Postgres.
- A dedicated research agent uses Tavily web search plus platform market tools to gather current ticker-specific updates, catalysts, and risk flags before strategy and risk decisions run.
- Strategy decisions are generated by an LLM through PydanticAI.
- Risk decisions are generated by an LLM, then constrained by deterministic guardrails.
- Execution defaults to direct Alpaca paper-trading REST using the signed-in user's own Alpaca connection for deployment reliability.
- The platform supports long and short stock workflows at the decision layer: buy to open longs, sell to close longs, sell to open shorts when allowed by the connected account, and buy to cover shorts.
- The copilot explains the latest run and helps operators understand next steps.
- The copilot can inspect saved run history, use the tool registry, launch scans and workflow runs, and run broker-aware execution actions when the user is authenticated with a connected Alpaca account.
- The copilot now uses a query-translation and intent-classification step before planning and action selection.
- The copilot now uses structured reasoning before answering or taking actions.
- The AI copilot streams visible reasoning updates in the workspace so users can see what it is checking before the final answer appears.
- Direct copilot commands such as running a scan, analyzing a ticker, buying a ticker, or approving a pending trade are routed into platform actions instead of staying as chat-only answers.
- Copilot-triggered runs now update the main homepage workspace the same way the main `Run` button does.
- When execution is pending confirmation, the copilot chat can surface inline `Approve` and `Decline` buttons, and approval uses the same backend confirmation flow as the main execution controls.
- The Day Session page lets operators configure a backend-managed intraday automation window with ticker selection, time window, interval, and timezone controls.

## Tool Catalog
- Market Data: `get_stock_bars`, `get_latest_quote`, `get_most_actives`, `check_market_clock`, `get_stock_news`, `search_web_research`
- Strategy: `calculate_rsi`, `calculate_macd`, `get_sec_filing`, `get_earnings_calendar`, `get_vix_level`, `get_sector_performance`
- Execution: `get_option_chain`, `place_option_order`, `get_crypto_bars`, `place_crypto_order`, `place_market_order`, `cancel_all_orders`, `close_all_positions`
- Risk: `get_account_balance`, `get_open_positions`, `set_stop_loss_order`, `get_portfolio_history`

## MCP Execution Note
- MCP is optional and intended for experimentation, not the default deployed path.
- Set `EXECUTION_PROVIDER=mcp` only if you explicitly want MCP-based order placement.
