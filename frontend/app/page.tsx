import { AuthHeader } from "./components/auth-header";
import { AuthConsole } from "./components/auth-console";
import { accountUrl } from "./components/auth-links";

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

const chatgptMcpUrl = "https://ledger.ttsseed.com/mcp";

const chatgptPrompts = [
  "List my accounts and current balances.",
  "Show my 20 latest transactions.",
  "What do I owe on each credit card?"
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
            <a className="primary-action" href="#chatgpt">
              Connect ChatGPT
            </a>
            <a className="secondary-action" href={accountUrl("login")}>
              Sign in
            </a>
          </div>
        </div>
      </section>

      <AuthConsole />

      <section id="chatgpt" className="chatgpt-band" aria-labelledby="chatgpt-title">
        <div className="chatgpt-intro">
          <p className="eyebrow">ChatGPT app</p>
          <h2 id="chatgpt-title">Connect your ledger to ChatGPT.</h2>
          <p className="chatgpt-lede">
            Ask about accounts, balances, and transactions in a normal conversation. OAuth gives ChatGPT read-only access only after you approve it.
          </p>
          <p className="chatgpt-account-note">
            Have your owner account ready. <a href={accountUrl("signup")}>Create it once</a> or <a href={accountUrl("login")}>sign in</a> before connecting.
          </p>

          <div className="chatgpt-address" aria-label="Track Anywhere MCP server address">
            <span>MCP server URL</span>
            <code>{chatgptMcpUrl}</code>
            <a href="https://chatgpt.com/plugins" target="_blank" rel="noreferrer">
              Open ChatGPT Plugins <span aria-hidden="true">↗</span>
            </a>
          </div>

          <p className="chatgpt-warning">
            Never paste a setup key or API key into ChatGPT. Sign-in and authorization happen only on ledger.ttsseed.com.
          </p>
        </div>

        <div className="chatgpt-guide">
          <ol className="chatgpt-steps">
            <li>
              <span className="chatgpt-step-number">01</span>
              <div>
                <h3>Enable Developer mode</h3>
                <p>In ChatGPT, open <strong>Settings → Security and login</strong> and turn on <strong>Developer mode</strong>.</p>
              </div>
            </li>
            <li>
              <span className="chatgpt-step-number">02</span>
              <div>
                <h3>Create the app</h3>
                <p>Open <strong>Settings → Plugins</strong>, select <strong>+</strong>, and name the app <strong>Track Anywhere</strong>.</p>
              </div>
            </li>
            <li>
              <span className="chatgpt-step-number">03</span>
              <div>
                <h3>Connect securely</h3>
                <p>Paste the MCP server URL above and create the app. ChatGPT discovers OAuth automatically; sign in here and approve <code>ledger:read</code>.</p>
              </div>
            </li>
            <li>
              <span className="chatgpt-step-number">04</span>
              <div>
                <h3>Start a conversation</h3>
                <p>Open a new chat, choose <strong>+ → More → Track Anywhere</strong>, then ask a ledger question.</p>
              </div>
            </li>
          </ol>

          <dl className="chatgpt-access" aria-label="ChatGPT access details">
            <div>
              <dt>Authentication</dt>
              <dd>OAuth + PKCE</dd>
            </div>
            <div>
              <dt>Permission</dt>
              <dd><code>ledger:read</code></dd>
            </div>
            <div>
              <dt>Access</dt>
              <dd>Read-only</dd>
            </div>
          </dl>

          <div className="chatgpt-prompts" aria-label="Prompt examples">
            <p>Try asking</p>
            <ul>
              {chatgptPrompts.map((prompt) => (
                <li key={prompt}>“{prompt}”</li>
              ))}
            </ul>
          </div>
        </div>
      </section>

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
