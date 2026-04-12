import Link from "next/link";

const sections = [
  {
    title: "What This Platform Does",
    intro:
      "Multi Agent Equity Trading Platform runs an end-to-end trading workflow from one workspace. It scans the market, selects a candidate, gathers market data, generates a strategy view, performs a risk check, and only then prepares an execution decision.",
    bullets: [
      "Guests can run analysis, inspect charts, review workflow state, and use the operator chat.",
      "Signed-in users can save history, connect their own broker, and approve execution when a trade is ready.",
      "The dashboard is designed to update in place on the homepage rather than moving you to a new screen after a run.",
      "The workspace now supports multi-ticker input, repeated run cycles, and optional auto execution for connected users.",
    ],
  },
  {
    title: "How A Run Works",
    intro:
      "Every run follows the coordinator-managed workflow. The system keeps shared state across the entire run and records what each specialist agent did.",
    bullets: [
      "Market scan: the scanner picks the strongest candidate unless you entered a manual ticker override.",
      "Market data: recent bars, quote context, and price history are loaded for the selected symbol.",
      "Strategy: the strategy agent evaluates the setup and produces signal, confidence, and reasoning.",
      "Risk: the risk agent applies buying power, liquidity, share sizing, and control checks.",
      "Execution: if the run passes risk and sizing checks, the system prepares a trade candidate instead of automatically sending an order.",
      "Validation loops: strategy, risk, and execution outputs are normalized and checked before the run is finalized.",
      "The run flow stays on the same homepage and updates the current workspace in place.",
    ],
  },
  {
    title: "Batch Tickers And Repeated Runs",
    intro:
      "You are not limited to one symbol at a time. The run control now supports sequential workflow execution across multiple tickers or repeated auto-selection cycles.",
    bullets: [
      "Enter multiple comma-separated or space-separated tickers such as AAPL, MSFT, NVDA.",
      "The platform runs those symbols one at a time and updates the workspace after each completed run.",
      "If you leave the ticker field blank, the system can repeat the autonomous scan for multiple cycles.",
      "The run summary toast reports how many runs completed and how many orders were submitted.",
    ],
  },
  {
    title: "Execution Approval",
    intro:
      "Execution is approval-gated. A normal run does not go straight to live order placement.",
    bullets: [
      "If a run passes risk, the workspace can return an execution status such as AWAITING_CONFIRMATION.",
      "Only a signed-in user with a connected execution-capable provider can approve the pending trade.",
      "Approval is limited to the most recent pending trade candidate for that user.",
      "The backend enforces approval rules too, so execution cannot bypass the confirmation step.",
    ],
  },
  {
    title: "Auto Execute Mode",
    intro:
      "The execution card now includes an Auto Execute switch for users who want approved runs to place orders immediately.",
    bullets: [
      "Auto Execute is only available when you are signed in and have a connected execution-capable broker.",
      "When enabled, qualifying runs can submit orders immediately instead of stopping at AWAITING_CONFIRMATION.",
      "The toggle is stored locally in your browser so the preference remains across refreshes.",
      "If Auto Execute is off, the workspace will continue using the approval-based execution flow.",
    ],
  },
  {
    title: "Auto Run Count",
    intro:
      "The execution card also includes an Auto Run Count control so you can repeat the workflow multiple times in sequence.",
    bullets: [
      "Use it with manual multi-ticker input to process several symbols back to back.",
      "Use it with an empty ticker field to repeat autonomous candidate selection across multiple cycles.",
      "This is especially useful when Auto Execute is enabled and you want to route more than one order without manually relaunching each run.",
      "Runs are executed sequentially, not all at once, so the workspace still shows a clear current state.",
    ],
  },
  {
    title: "Accounts And Connections",
    intro:
      "Execution never uses a shared platform trading account. Provider connections are user-specific.",
    bullets: [
      "You can sign in with email/password or Google sign-in when configured.",
      "Broker credentials are stored per user and are intended for your own connected account.",
      "The current execution path is centered on Alpaca paper trading for safer testing and deployment reliability.",
      "If no broker is connected, the platform stays in analysis mode and explains that execution is unavailable.",
    ],
  },
  {
    title: "AI Copilot",
    intro:
      "The AI Copilot is more than a static FAQ. It can use platform context and take actions when appropriate.",
    bullets: [
      "It can explain the latest run, compare recent runs, summarize reasoning, and answer questions about risk or execution status.",
      "It can inspect saved history when you are signed in.",
      "It can scan the market, launch analysis runs, and call tools through the centralized tool registry.",
      "If you ask it to execute a trade, it still prepares the trade candidate for approval rather than bypassing execution safeguards.",
    ],
  },
  {
    title: "Available Tooling",
    intro:
      "The shared tool registry powers market, strategy, risk, and execution operations across the coordinator and copilot.",
    bullets: [
      "Market data: stock bars, latest quotes, most active symbols, market clock, and stock news.",
      "Strategy and macro: RSI, MACD, SEC filings, earnings calendar, VIX, and sector performance.",
      "Execution: option chains, option orders, crypto bars, crypto orders, market orders, cancel-all-orders, and close-all-positions.",
      "Risk and portfolio: account balance, open positions, stop-loss orders, and portfolio history.",
    ],
  },
  {
    title: "History And Traceability",
    intro:
      "Signed-in users get persistent run history and deeper operational context.",
    bullets: [
      "Recent runs are saved and shown in the history view.",
      "The workspace can expose decision trail, tool history, validation reports, and raw workflow state for debugging.",
      "Copilot responses can use multiple previous runs instead of relying only on the current in-memory state.",
    ],
  },
  {
    title: "Manual Override Vs Auto Selection",
    intro:
      "You can let the platform choose a candidate or direct it to inspect a specific symbol.",
    bullets: [
      "Leave the ticker field blank to let the scanner choose from the configured universe.",
      "Enter one ticker to force a manual override for that run, or enter several to process them in sequence.",
      "The workspace labels whether the selected symbol came from auto selection or manual override.",
    ],
  },
  {
    title: "What To Do When Something Fails",
    intro:
      "A few issues are normal during setup, especially around local development and external providers.",
    bullets: [
      "If the backend cannot be reached, confirm the FastAPI service is running and your frontend API URL points to the right backend.",
      "If Google sign-in does not appear, verify the frontend Google client ID and OAuth allowed origins.",
      "If broker execution fails, reconnect your Alpaca credentials and confirm they are valid paper-trading keys.",
      "If a run is blocked, check the risk gate, the execution detail message, and the raw workflow state panel for the exact reason.",
    ],
  },
];

export default function HelpPage() {
  return (
    <main className="help-shell">
      <div className="help-header">
        <div>
          <p className="eyebrow">Help</p>
          <h1>Platform Guide</h1>
          <p className="help-subtitle">
            Use this page to understand how runs work, when execution is allowed, what the operator chat can do, and how the platform is intended to be used day to day.
          </p>
        </div>
        <Link href="/" className="help-link">
          Back To Workspace
        </Link>
      </div>

      <section className="help-grid">
        {sections.map((section) => (
          <article key={section.title} className="help-card">
            <h2>{section.title}</h2>
            <p>{section.intro}</p>
            <ul className="help-list">
              {section.bullets.map((bullet) => (
                <li key={bullet}>{bullet}</li>
              ))}
            </ul>
          </article>
        ))}
      </section>
    </main>
  );
}
