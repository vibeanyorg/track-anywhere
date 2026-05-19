import { AuthHeader } from "./components/auth-header";
import { AuthConsole } from "./components/auth-console";

const operatingLines = [
  "Drafts stay editable until reviewed.",
  "Confirmed entries stay balanced and auditable.",
  "CLI, MCP, and API surfaces share one contract."
];

const workflow = [
  { label: "Capture", value: "coffee 32 CNY", tone: "warm" },
  { label: "Review", value: "2 drafts ready", tone: "moss" },
  { label: "Confirm", value: "balanced ledger", tone: "ink" }
];

export default function Page() {
  return (
    <main>
      <section className="hero" aria-labelledby="hero-title">
        <div className="ledger-scene" aria-hidden="true">
          <div className="scene-column scene-column-left">
            <span>x-api-key</span>
            <span>oauth.authorize</span>
            <span>pkce.exchange</span>
            <span>credential.scope</span>
          </div>
          <div className="scene-ledger">
            <div className="ledger-line ledger-line-head">
              <span>Today</span>
              <span>CNY</span>
              <span>State</span>
            </div>
            <div className="ledger-line">
              <span>Coffee</span>
              <span>-32.00</span>
              <span>draft</span>
            </div>
            <div className="ledger-line">
              <span>Salary</span>
              <span>+18,500.00</span>
              <span>confirmed</span>
            </div>
            <div className="ledger-line">
              <span>Brokerage</span>
              <span>+412.20</span>
              <span>synced</span>
            </div>
          </div>
          <div className="scene-ribbon">platform token active</div>
        </div>

        <AuthHeader />

        <div className="hero-copy">
          <p className="eyebrow">Track Anywhere</p>
          <h1 id="hero-title">Track Anywhere</h1>
          <p className="hero-summary">
            A personal accounting workspace for draft-first capture, strict ledger confirmation, and agent-ready money workflows.
          </p>
          <div className="hero-actions" aria-label="Primary actions">
            <a className="primary-action" href="#workflow">
              See workflow
            </a>
            <a className="secondary-action" href="#auth">
              Open auth
            </a>
          </div>
        </div>
      </section>

      <AuthConsole />

      <section id="workflow" className="workflow-band" aria-label="Workflow">
        <div className="workflow-copy">
          <p className="eyebrow">Operating model</p>
          <h2>Capture fast. Confirm deliberately.</h2>
        </div>
        <div className="workflow-grid">
          {workflow.map((item) => (
            <article className={`workflow-item workflow-${item.tone}`} key={item.label}>
              <p>{item.label}</p>
              <strong>{item.value}</strong>
            </article>
          ))}
        </div>
      </section>

      <section className="principles-band" aria-label="Principles">
        <div className="principles-list">
          {operatingLines.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </div>
      </section>
    </main>
  );
}
