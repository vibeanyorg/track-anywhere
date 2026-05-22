"use client";

import { useMemo, useState } from "react";
import { useAuth } from "./auth-provider";
import { readJson, responseError } from "../lib/http";

const defaultScope = "account:read book:read ledger:read";
const defaultRedirectUri = "http://127.0.0.1:3000/auth/callback";

type CredentialSummary = {
  credential_id: string;
  key_prefix: string;
  scopes: string[];
  issued_at: string;
  expires_at: string;
  revoked_at: string | null;
  active: boolean;
};

type OAuthClientSummary = {
  client_id: string;
  client_name: string;
  redirect_uris: string[];
  scope: string;
  client_uri?: string | null;
};

export function AuthConsole() {
  const { session, credentials, clients, refreshCredentials, refreshClients } = useAuth();

  const [activeTab, setActiveTab] = useState<"keys" | "oauth">("keys");
  const [keyScopes, setKeyScopes] = useState(defaultScope);
  const [keyTtl, setKeyTtl] = useState("60");
  const [createdKey, setCreatedKey] = useState("");
  const [clientName, setClientName] = useState("Local MCP Client");
  const [redirectUri, setRedirectUri] = useState(defaultRedirectUri);
  const [clientScope, setClientScope] = useState(defaultScope);
  const [selectedClientId, setSelectedClientId] = useState("track-anywhere-web");
  const [connectedToken, setConnectedToken] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const displayName = useMemo(() => {
    return session.identity?.name || session.identity?.email || "You";
  }, [session]);

  if (!session.authenticated) {
    return null;
  }

  async function generateKey() {
    await runBusy("Creating key", async () => {
      const payload = await postJson<{ credential?: { token?: string } }>(
        "/api/v1/credentials/agent",
        { scopes: parseScopes(keyScopes), ttl_minutes: Number(keyTtl) },
        true
      );
      setCreatedKey(payload.credential?.token ?? "");
      await refreshCredentials();
      setMessage("Key created. Copy it now — you won't see it again.");
    });
  }

  async function revokeKey(credentialId: string) {
    await runBusy("Revoking", async () => {
      await postJson(
        `/api/v1/credentials/${encodeURIComponent(credentialId)}/revoke`,
        { reason: "revoked from web console" },
        true
      );
      await refreshCredentials();
      setMessage("Key revoked.");
    });
  }

  async function addApp() {
    await runBusy("Adding app", async () => {
      const client = await postJson<OAuthClientSummary>("/api/v1/oauth/register", {
        client_name: clientName,
        redirect_uris: [redirectUri],
        scope: clientScope,
        token_endpoint_auth_method: "none"
      });
      await refreshClients();
      setSelectedClientId(client.client_id);
      setMessage("App added.");
    });
  }

  async function connectApp() {
    const client = clients.find((item) => item.client_id === selectedClientId);
    const targetRedirect = client?.redirect_uris[0] ?? redirectUri;
    const scope = client?.scope || clientScope;
    await runBusy("Connecting", async () => {
      const token = await runPkceExchange(selectedClientId, targetRedirect, scope);
      setConnectedToken(token);
      setMessage("Access token issued.");
    });
  }

  async function copyValue(value: string, successMessage: string) {
    if (!value) return;
    await navigator.clipboard.writeText(value);
    setMessage(successMessage);
  }

  async function runBusy(_label: string, action: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    try {
      await action();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="auth-console-band" aria-label="Account settings">
      <div className="auth-console-shell">
        <div className="auth-console-heading">
          <p className="eyebrow">Account</p>
          <h2>Keys and connected apps</h2>
          {message ? <span className="auth-state auth-state-on">{message}</span> : null}
        </div>

        <div className="auth-console-layout">
          <div className="auth-console-sidebar">
            <p>{displayName}</p>
            <button
              className={activeTab === "keys" ? "console-tab console-tab-active" : "console-tab"}
              type="button"
              onClick={() => setActiveTab("keys")}
            >
              API keys
            </button>
            <button
              className={activeTab === "oauth" ? "console-tab console-tab-active" : "console-tab"}
              type="button"
              onClick={() => setActiveTab("oauth")}
            >
              Connected apps
            </button>
          </div>

          {activeTab === "keys" ? (
            <div className="auth-console-panel">
              <div className="console-panel-head">
                <h3>API keys</h3>
                <button className="text-button text-button-strong" type="button" onClick={generateKey} disabled={busy}>
                  New key
                </button>
              </div>
              <p className="console-helper">Use these to access your data from a script or the command line.</p>
              <label className="auth-label">
                What it can do
                <input
                  id="key-scopes"
                  name="key_scopes"
                  className="auth-input auth-input-wide"
                  value={keyScopes}
                  onChange={(event) => setKeyScopes(event.target.value)}
                />
              </label>
              <label className="auth-label">
                Expires in (minutes)
                <input
                  id="key-ttl"
                  name="key_ttl"
                  className="auth-input auth-input-small"
                  type="number"
                  min="1"
                  max="1440"
                  value={keyTtl}
                  onChange={(event) => setKeyTtl(event.target.value)}
                />
              </label>
              {createdKey ? (
                <SecretOutput value={createdKey} label="Your new key" onCopy={() => copyValue(createdKey, "Key copied.")} />
              ) : null}
              <CredentialList credentials={credentials} onRevoke={revokeKey} busy={busy} />
            </div>
          ) : (
            <div className="auth-console-panel">
              <div className="console-panel-head">
                <h3>Connected apps</h3>
                <button className="text-button text-button-strong" type="button" onClick={connectApp} disabled={busy}>
                  Connect
                </button>
              </div>
              <p className="console-helper">Give an app limited access without sharing your password.</p>
              <label className="auth-label">
                App
                <select
                  id="oauth-client"
                  name="oauth_client"
                  className="auth-input auth-input-wide"
                  value={selectedClientId}
                  onChange={(event) => setSelectedClientId(event.target.value)}
                >
                  {clients.map((client) => (
                    <option value={client.client_id} key={client.client_id}>
                      {client.client_name}
                    </option>
                  ))}
                </select>
              </label>
              <div className="oauth-client-form">
                <input
                  id="oauth-client-name"
                  name="oauth_client_name"
                  className="auth-input auth-input-wide"
                  value={clientName}
                  onChange={(event) => setClientName(event.target.value)}
                  aria-label="App name"
                  placeholder="App name"
                />
                <input
                  id="oauth-redirect-uri"
                  name="oauth_redirect_uri"
                  className="auth-input auth-input-wide"
                  value={redirectUri}
                  onChange={(event) => setRedirectUri(event.target.value)}
                  aria-label="Where the app receives the response"
                  placeholder="Where the app receives the response"
                />
                <input
                  id="oauth-client-scope"
                  name="oauth_client_scope"
                  className="auth-input auth-input-wide"
                  value={clientScope}
                  onChange={(event) => setClientScope(event.target.value)}
                  aria-label="What it can do"
                  placeholder="What it can do"
                />
                <button className="text-button" type="button" onClick={addApp} disabled={busy}>
                  Add app
                </button>
              </div>
              {connectedToken ? (
                <SecretOutput value={connectedToken} label="Access token" onCopy={() => copyValue(connectedToken, "Token copied.")} />
              ) : null}
              <OAuthClientList clients={clients} />
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function CredentialList({
  credentials,
  onRevoke,
  busy
}: {
  credentials: CredentialSummary[];
  onRevoke: (id: string) => void;
  busy: boolean;
}) {
  if (credentials.length === 0) {
    return <p className="console-empty">No keys yet.</p>;
  }
  return (
    <div className="console-list">
      {credentials.map((credential) => (
        <div className="console-row" key={credential.credential_id}>
          <div>
            <strong>{credential.key_prefix}</strong>
            <p>{credential.scopes.join(" · ")}</p>
            <small>
              {credential.active ? "Active" : "Revoked"} · expires {formatDate(credential.expires_at)}
            </small>
          </div>
          {credential.active ? (
            <button className="text-button" type="button" onClick={() => onRevoke(credential.credential_id)} disabled={busy}>
              Revoke
            </button>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function OAuthClientList({ clients }: { clients: OAuthClientSummary[] }) {
  if (clients.length === 0) {
    return <p className="console-empty">No apps connected yet.</p>;
  }
  return (
    <div className="console-list">
      {clients.map((client) => (
        <div className="console-row" key={client.client_id}>
          <div>
            <strong>{client.client_name}</strong>
            <p>{client.client_id}</p>
            <small>{client.scope}</small>
          </div>
        </div>
      ))}
    </div>
  );
}

function SecretOutput({ value, label, onCopy }: { value: string; label: string; onCopy: () => void }) {
  return (
    <div className="secret-output">
      <span>{label}</span>
      <code>{value}</code>
      <button className="text-button" type="button" onClick={onCopy}>
        Copy
      </button>
    </div>
  );
}

async function postJson<T>(url: string, payload: object, csrf = false): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(csrf
        ? {
            "X-CSRF-Token": readCookie("ta_csrf"),
            "X-Idempotency-Key": `web-${crypto.randomUUID()}`
          }
        : {})
    },
    body: JSON.stringify(payload)
  });
  const body = await readJson<T & { detail?: string }>(response);
  if (!response.ok) throw new Error(responseError(body));
  return body;
}

async function runPkceExchange(clientId: string, redirectUri: string, scope: string) {
  const state = randomBase64Url(18);
  const verifier = randomBase64Url(48);
  const authorize = await postJson<{ redirect_uri?: string }>(
    "/api/v1/oauth/authorize",
    {
      client_id: clientId,
      redirect_uri: redirectUri,
      scope,
      state,
      code_challenge: await pkceChallenge(verifier),
      code_challenge_method: "S256",
      action: "approve"
    },
    true
  );
  const callbackUrl = new URL(authorize.redirect_uri ?? "");
  const code = callbackUrl.searchParams.get("code");
  if (!code || callbackUrl.searchParams.get("state") !== state) throw new Error("The app didn't respond correctly.");
  const token = await postJson<{ access_token?: string }>("/api/v1/oauth/token", {
    grant_type: "authorization_code",
    code,
    client_id: clientId,
    redirect_uri: redirectUri,
    code_verifier: verifier
  });
  if (!token.access_token) throw new Error("No access token received.");
  return token.access_token;
}

function parseScopes(scopeText: string) {
  return scopeText
    .split(/\s+/)
    .map((scope) => scope.trim())
    .filter(Boolean);
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function readCookie(name: string) {
  const value = document.cookie.split("; ").find((item) => item.startsWith(`${name}=`))?.split("=")[1];
  return value ? decodeURIComponent(value) : "";
}

function randomBase64Url(byteLength: number) {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return base64Url(bytes);
}

async function pkceChallenge(verifier: string) {
  const data = new TextEncoder().encode(verifier);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return base64Url(new Uint8Array(digest));
}

function base64Url(bytes: Uint8Array) {
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}
