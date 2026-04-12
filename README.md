# Multi Agent Equity Trading Platform

This project is now split into two deployable applications:

- `frontend/` for the operator dashboard, intended for Vercel
- `backend/` for the API, intended for Heroku

The backend handles LangGraph orchestration, PydanticAI decision agents, market data, risk checks, execution, and the AI copilot. The frontend is now a real Next.js application that calls the backend over HTTP.

## Structure
- `frontend/` - Next.js frontend for Vercel
- `frontend/app/` - App Router pages and styles
- `frontend/package.json` - frontend dependencies and scripts
- `backend/` - FastAPI API service
- `backend/agents/` - market, strategy, risk, execution, and copilot agents
- `backend/Procfile` - Heroku process definition
- `backend/requirements.txt` - backend Python dependencies
- `backend/models.py` - user model persisted to Heroku Postgres
- `backend/auth.py` - JWT, password auth, and Google auth helpers

## Local Development

### Backend

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
# source .venv/bin/activate

pip install -r requirements.txt
uvicorn app:app --reload
```

Backend environment variables:
- `DATABASE_URL`
- `JWT_SECRET_KEY`
- `ACCESS_TOKEN_EXPIRE_MINUTES`
- `GOOGLE_CLIENT_ID`
- `BROKER_CREDENTIALS_ENCRYPTION_KEY`
- `POLYGON_API_KEY`
- `OPENAI_API_KEY`
- `PYDANTIC_AI_MODEL`
- `LANGSMITH_TRACING`
- `LANGSMITH_API_KEY`
- `LANGSMITH_PROJECT`
- `AUTO_TRADE_UNIVERSE`
- `EXECUTION_PROVIDER`
- `CORS_ALLOW_ORIGINS`

Example:

```env
DATABASE_URL=postgresql+psycopg://username:password@hostname:5432/database_name
JWT_SECRET_KEY=change-me-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=120
GOOGLE_CLIENT_ID=your_google_oauth_client_id.apps.googleusercontent.com
BROKER_CREDENTIALS_ENCRYPTION_KEY=replace_with_a_32_byte_secret_or_fernet_key
OPENAI_API_KEY=your_openai_api_key
PYDANTIC_AI_MODEL=openai:gpt-4.1-mini
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your_langsmith_api_key
LANGSMITH_PROJECT=multi-agent-equity-trading-platform
AUTO_TRADE_UNIVERSE=NVDA,MSFT,AAPL,AMZN,META,GOOGL,AVGO,TSLA,AMD,NFLX
EXECUTION_PROVIDER=alpaca_rest
CORS_ALLOW_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,https://your-vercel-app.vercel.app
```

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

## Deployment

### Deploy Frontend To Vercel
1. Point Vercel at the `frontend/` directory.
2. Set `NEXT_PUBLIC_API_BASE_URL` to your deployed Heroku backend URL.
3. Set `NEXT_PUBLIC_GOOGLE_CLIENT_ID` to your Google OAuth client ID.
4. Deploy as a Next.js app.

### Deploy Backend To Heroku
1. Create or use a Heroku app for the backend.
2. Attach Heroku Postgres and set `DATABASE_URL`.
3. Deploy the `backend/` directory as the Heroku app.
4. Configure the backend environment variables on Heroku.
5. Set `CORS_ALLOW_ORIGINS` to include your Vercel frontend origin.
6. Heroku will use `backend/Procfile` to start the API.

Example git-based deploy from the repo root:

```bash
git init
git add .
git commit -m "Initial backend deploy"
heroku apps:create your-backend-app-name
heroku git:remote -a your-backend-app-name
git subtree push --prefix backend heroku main
```

Heroku Python runtime is pinned with `backend/.python-version`.

## API Endpoints
- `GET /api/health` - backend health check
- `POST /api/auth/register` - create a local user and return a JWT
- `POST /api/auth/login` - local email/password login returning a JWT
- `POST /api/auth/google` - exchange a Google credential for a JWT
- `GET /api/auth/me` - fetch the current authenticated user
- `GET /api/integrations/providers` - list supported provider types and their credential requirements
- `GET /api/integrations` - list the signed-in user's connected providers
- `GET /api/integrations/{provider}` - fetch one signed-in user's provider connection
- `POST /api/integrations/{provider}` - save or update a provider connection
- `DELETE /api/integrations/{provider}` - disconnect a provider
- `POST /api/run` - run the autonomous workflow, with optional manual ticker override
- `POST /api/copilot` - ask the AI copilot about the latest workflow state

## LangSmith Observability
- LangSmith tracing can be enabled entirely through backend environment variables.
- LangGraph workflow runs, API endpoints, and PydanticAI agent calls are prepared for LangSmith tracing.
- Set `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, and optionally `LANGSMITH_PROJECT` on Heroku.
- Backend health responses now report whether tracing is enabled.

## Platform Design
- The default behavior is autonomous: scan a configured equity universe, pick the strongest momentum candidate, and run the trading workflow on that selection.
- Signing in is optional for public users. Guests can scan, analyze, and use the copilot.
- Users authenticate with JWT-backed sessions, with local email/password auth and Google sign-in support.
- User records are stored in Postgres, which maps directly to Heroku Postgres for deployment.
- Provider credentials are not shared from backend environment variables. Signed-in users connect their own providers, and those credentials are stored encrypted in Postgres.
- Strategy decisions are generated by an LLM through PydanticAI.
- Risk decisions are generated by an LLM, then constrained by deterministic guardrails.
- Execution defaults to direct Alpaca paper-trading REST using the signed-in user's own Alpaca connection for deployment reliability.
- The copilot explains the latest run and helps operators understand next steps.

## MCP Execution Note
- MCP is optional and intended for experimentation, not the default deployed path.
- Set `EXECUTION_PROVIDER=mcp` only if you explicitly want MCP-based order placement.

## Notes
- The frontend and backend are intentionally decoupled for independent deployment.
- The copilot currently uses the latest backend workflow state held in memory.
- Persistent authenticated run history is stored in the configured database.
