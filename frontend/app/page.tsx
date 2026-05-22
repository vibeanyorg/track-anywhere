import { AuthHeader } from "./components/auth-header";
import { AuthConsole } from "./components/auth-console";

const operatingLines = [
  "Edit anything until you confirm it.",
  "Once you confirm, numbers don't change behind your back.",
  "Same data from the app, the command line, or an AI assistant."
];

const workflow = [
  { label: "Jot it down", value: "Coffee · ¥32", tone: "warm" },
  { label: "Look it over", value: "2 to review", tone: "moss" },
  { label: "Settle the books", value: "All balanced", tone: "ink" }
];

export default function Page() {
  return (
    <main>
      <section className="hero" aria-labelledby="hero-title">
        <div className="ledger-scene" aria-hidden="true">
          <div className="scene-column scene-column-left">
            <span>Coffee</span>
            <span>Groceries</span>
            <span>Rent</span>
            <span>Salary</span>
          </div>
          <div className="scene-ledger">
            <div className="ledger-line ledger-line-head">
              <span>Today</span>
              <span>CNY</span>
              <span>Status</span>
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
              <span>Stocks</span>
              <span>+412.20</span>
              <span>synced</span>
            </div>
          </div>
          <div className="scene-ribbon">All synced</div>
        </div>

        <AuthHeader />

        <div className="hero-copy">
          <p className="eyebrow">Personal accounting</p>
          <h1 id="hero-title">Track Anywhere</h1>
          <p className="hero-summary">
            Write down what you spend — from the app, your terminal, or a chat. The numbers stay yours, and they stay in one place.
          </p>
          <div className="hero-actions" aria-label="Primary actions">
            <a className="primary-action" href="#workflow">
              How it works
            </a>
            <a className="secondary-action" href="#auth">
              Sign in
            </a>
          </div>
        </div>
      </section>

      <AuthConsole />

      <section id="workflow" className="workflow-band" aria-label="How it works">
        <div className="workflow-copy">
          <p className="eyebrow">How it works</p>
          <h2>Write it down. Look it over. Settle the books.</h2>
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

      <section className="principles-band" aria-label="What you get">
        <div className="principles-list">
          {operatingLines.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </div>
      </section>
    </main>
  );
}
