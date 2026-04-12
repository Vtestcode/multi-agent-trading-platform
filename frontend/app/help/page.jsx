import Link from "next/link";

const sections = [
  {
    title: "Overview",
    body:
      "Multi Agent Equity Trading Platform combines market selection, strategy, risk review, execution, and AI assistance in one workspace. Guests can explore analysis immediately, while signed-in users can connect their own providers for execution and personalized integrations.",
  },
  {
    title: "How Runs Work",
    body:
      "Each run starts with candidate selection, followed by strategy generation, risk review, and execution routing. Manual ticker input is optional and acts as an override when you want to inspect a specific symbol.",
  },
  {
    title: "Execution Access",
    body:
      "Trades are never executed from a shared platform account. To enable execution, sign in and connect your own execution-capable provider such as Alpaca paper trading.",
  },
  {
    title: "Copilot",
    body:
      "The built-in assistant can explain recent runs, summarize reasoning, and help diagnose why a trade was approved, rejected, or skipped.",
  },
  {
    title: "Integrations",
    body:
      "The platform supports a provider model for broker, AI, observability, and data connections. Credentials are stored per user and are not shared across the workspace.",
  },
];

export default function HelpPage() {
  return (
    <main className="help-shell">
      <div className="help-header">
        <div>
          <p className="eyebrow">Help</p>
          <h1>Using the platform</h1>
          <p className="help-subtitle">Everything you need to understand the workspace, runs, execution access, and integrations.</p>
        </div>
        <Link href="/" className="help-link">
          Back To Workspace
        </Link>
      </div>

      <section className="help-grid">
        {sections.map((section) => (
          <article key={section.title} className="help-card">
            <h2>{section.title}</h2>
            <p>{section.body}</p>
          </article>
        ))}
      </section>
    </main>
  );
}
