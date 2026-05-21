"use client";

import { useEffect, useMemo, useState } from "react";
import { accountUrl } from "./auth-links";
import { readJson, responseError } from "../lib/http";

type SessionResponse = {
  authenticated: boolean;
  identity: {
    name?: string | null;
    email?: string | null;
    role?: string | null;
  } | null;
};

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

const defaultScope = "account:read book:read ledger:read";
const defaultRedirectUri = "http://127.0.0.1:3000/auth/callback";

export function AuthConsole() {
  const [session, setSession] = useState<SessionResponse>({ authenticated: false, identity: null });
  const [credentials, setCredentials] = useState<CredentialSummary[]>([]);
  const [clients, setClients] = useState<OAuthClientSummary[]>([]);
  const [activeTab, setActiveTab] = useState<"keys" | "oauth">("keys");
  const [apiKey, setApiKey] = useState("");
  const [keyScopes, setKeyScopes] = useState(defaultScope);
  const [keyTtl, setKeyTtl] = useState("60");
  const [createdKey, setCreatedKey] = useState("");
  const [clientName, setClientName] = useState("Local MCP Client");
  const [redirectUri, setRedirectUri] = useState(defaultRedirectUri);
  const [clientScope, setClientScope] = useState(defaultScope);
  const [selectedClientId, setSelectedClientId] = useState("track-anywhere-web");
  const [connectedToken, setConnectedToken] = useState("");
  const [status, setStatus] = useState("Ready");
  const [busy, setBusy] = useState(false);

  const displayName = useMemo(() => {
    return session.identity?.name || session.identity?.email || "Authenticated";
  }, [session]);

  async function refresh(nextStatus?: string) {
    const nextSession = await fetchJson<SessionResponse>("/api/v1/auth/session");
    setSession(nextSession);
    if (nextSession.authenticated) {
      await Promise.all([refreshCredentials(), refreshClients()]);
    } else {
      setCredentials([]);
      setClients([]);
    }
    setStatus(nextStatus ?? (nextSession.authenticated ? "Signed in" : "Ready"));
  }

  async function refreshCredentials() {
    const response = await fetch("/api/v1/credentials", { credentials: "include", cache: "no-store" });
    if (response.ok) {
      const payload = await readJson<{ credentials?: CredentialSummary[] }>(response);
      setCredentials(payload.credentials ?? []);
    }
  }

  async function refreshClients() {
    const response = await fetch("/api/v1/oauth/clients", { credentials: "include", cache: "no-store" });
    if (response.ok) {
      const payload = await readJson<{ clients?: OAuthClientSummary[] }>(response);
      const nextClients = payload.clients ?? [];
      setClients(nextClients);
      if (!nextClients.some((client) => client.client_id === selectedClientId)) {
        setSelectedClientId(nextClients[0]?.client_id ?? "track-anywhere-web");
      }
    }
  }

  async function loginWithApiKey() {
    if (!apiKey.trim()) {
      setStatus("API key required");
      return;
    }
    await runBusy("Signing in", async () => {
      await postJson("/api/v1/auth/session/api-key", { api_key: apiKey.trim() });
      setApiKey("");
      await refresh("API key accepted");
      notifyAuthChanged();
    });
  }

  async function startLocalSession() {
    await runBusy("Opening local session", async () => {
      await postJson("/api/v1/session/dev-local", {});
      await refresh("Local session active");
      notifyAuthChanged();
    });
  }

  async function createApiKey() {
    const scopes = parseScopes(keyScopes);
    await runBusy("Creating API key", async () => {
      const payload = await postJson<{ credential?: { token?: string } }>("/api/v1/credentials/agent", {
        scopes,
        ttl_minutes: Number(keyTtl),
      }, true);
      setCreatedKey(payload.credential?.token ?? "");
      await refreshCredentials();
      setStatus("API key created");
    }, true);
  }

  async function revokeCredential(credentialId: string) {
    await runBusy("Revoking API key", async () => {
      await postJson(`/api/v1/credentials/${encodeURIComponent(credentialId)}/revoke`, {
        reason: "revoked from web console",
      }, true);
      await refreshCredentials();
      setStatus("API key revoked");
    }, true);
  }

  async function registerClient() {
    await runBusy("Registering OAuth client", async () => {
      const client = await postJson<OAuthClientSummary>("/api/v1/oauth/register", {
        client_name: clientName,
        redirect_uris: [redirectUri],
        scope: clientScope,
        token_endpoint_auth_method: "none",
      });
      await refreshClients();
      setSelectedClientId(client.client_id);
      setStatus("OAuth client registered");
    });
  }

  async function connectOAuthClient() {
    const client = clients.find((item) => item.client_id === selectedClientId);
    const targetRedirect = client?.redirect_uris[0] ?? redirectUri;
    const scope = client?.scope || clientScope;
    await runBusy("Authorizing OAuth client", async () => {
      const token = await runPkceExchange(selectedClientId, targetRedirect, scope);
      setConnectedToken(token);
      setStatus("OAuth token issued");
    });
  }

  async function copyValue(value: string, nextStatus: string) {
    if (!value) return;
    await navigator.clipboard.writeText(value);
    setStatus(nextStatus);
  }

  async function runBusy(label: string, action: () => Promise<void>, csrf = false) {
    setBusy(true);
    setStatus(label);
    try {
      await actionWithCsrf(action, csrf);
    } catch {
      setStatus("Request failed");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void refresh();
    const handleAuthChanged = () => {
      void refresh();
    };
    window.addEventListener("track-anywhere-auth-changed", handleAuthChanged);
    return () => window.removeEventListener("track-anywhere-auth-changed", handleAuthChanged);
  }, []);

  return (
    <section className="auth-console-band" aria-label="Authentication console">
      <div className="auth-console-shell">
        <div className="auth-console-heading">
          <p className="eyebrow">Auth console</p>
          <h2>Account, API keys, OAuth connect.</h2>
          <span className={`auth-state ${session.authenticated ? "auth-state-on" : ""}`}>{status}</span>
        </div>

        {!session.authenticated ? (
          <div className="auth-access-grid">
            <div className="auth-console-panel">
              <h3>Login or register</h3>
              <div className="auth-action-row">
                <a className="text-button text-button-strong" href={accountUrl("login")}>
                  Log in
                </a>
                <a className="text-button" href={accountUrl("signup")}>
                  Register
                </a>
                <button className="text-button" type="button" onClick={startLocalSession} disabled={busy}>
                  Local
                </button>
              </div>
            </div>
            <div className="auth-console-panel">
              <h3>API key session</h3>
              <div className="auth-field-row">
                <input id="console-api-key" name="console_api_key" className="auth-input auth-input-wide" type="password" value={apiKey} placeholder="ta_ API key" onChange={(event) => setApiKey(event.target.value)} />
                <button className="text-button text-button-strong" type="button" onClick={loginWithApiKey} disabled={busy}>
                  Connect
                </button>
              </div>
            </div>
          </div>
        ) : (
          <div className="auth-console-layout">
            <div className="auth-console-sidebar">
              <p>{displayName}</p>
              <button className={activeTab === "keys" ? "console-tab console-tab-active" : "console-tab"} type="button" onClick={() => setActiveTab("keys")}>
                API keys
              </button>
              <button className={activeTab === "oauth" ? "console-tab console-tab-active" : "console-tab"} type="button" onClick={() => setActiveTab("oauth")}>
                OAuth connect
              </button>
            </div>

            {activeTab === "keys" ? (
              <div className="auth-console-panel">
                <div className="console-panel-head">
                  <h3>API keys</h3>
                  <button className="text-button text-button-strong" type="button" onClick={createApiKey} disabled={busy}>
                    Create key
                  </button>
                </div>
                <label className="auth-label">
                  Scopes
                  <input id="key-scopes" name="key_scopes" className="auth-input auth-input-wide" value={keyScopes} onChange={(event) => setKeyScopes(event.target.value)} />
                </label>
                <label className="auth-label">
                  TTL minutes
                  <input id="key-ttl" name="key_ttl" className="auth-input auth-input-small" type="number" min="1" max="1440" value={keyTtl} onChange={(event) => setKeyTtl(event.target.value)} />
                </label>
                {createdKey ? <SecretOutput value={createdKey} label="Created key" onCopy={() => copyValue(createdKey, "API key copied")} /> : null}
                <CredentialList credentials={credentials} onRevoke={revokeCredential} busy={busy} />
              </div>
            ) : (
              <div className="auth-console-panel">
                <div className="console-panel-head">
                  <h3>OAuth connect</h3>
                  <button className="text-button text-button-strong" type="button" onClick={connectOAuthClient} disabled={busy}>
                    Connect
                  </button>
                </div>
                <label className="auth-label">
                  Client
                  <select id="oauth-client" name="oauth_client" className="auth-input auth-input-wide" value={selectedClientId} onChange={(event) => setSelectedClientId(event.target.value)}>
                    {clients.map((client) => (
                      <option value={client.client_id} key={client.client_id}>
                        {client.client_name}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="oauth-client-form">
                  <input id="oauth-client-name" name="oauth_client_name" className="auth-input auth-input-wide" value={clientName} onChange={(event) => setClientName(event.target.value)} aria-label="OAuth client name" />
                  <input id="oauth-redirect-uri" name="oauth_redirect_uri" className="auth-input auth-input-wide" value={redirectUri} onChange={(event) => setRedirectUri(event.target.value)} aria-label="OAuth redirect URI" />
                  <input id="oauth-client-scope" name="oauth_client_scope" className="auth-input auth-input-wide" value={clientScope} onChange={(event) => setClientScope(event.target.value)} aria-label="OAuth scopes" />
                  <button className="text-button" type="button" onClick={registerClient} disabled={busy}>
                    Register client
                  </button>
                </div>
                {connectedToken ? <SecretOutput value={connectedToken} label="OAuth access token" onCopy={() => copyValue(connectedToken, "OAuth token copied")} /> : null}
                <OAuthClientList clients={clients} />
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

function CredentialList({ credentials, onRevoke, busy }: { credentials: CredentialSummary[]; onRevoke: (id: string) => void; busy: boolean }) {
  if (credentials.length === 0) {
    return <p className="console-empty">No API keys yet.</p>;
  }
  return (
    <div className="console-list">
      {credentials.map((credential) => (
        <div className="console-row" key={credential.credential_id}>
          <div>
            <strong>{credential.key_prefix}</strong>
            <p>{credential.scopes.join(" ")}</p>
            <small>{credential.active ? "active" : "revoked"} · expires {formatDate(credential.expires_at)}</small>
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

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { credentials: "include", cache: "no-store" });
  const payload = await readJson<T & { detail?: string }>(response);
  if (!response.ok) throw new Error(responseError(payload));
  return payload;
}

async function postJson<T>(url: string, payload: object, csrf = false): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(csrf ? { "X-CSRF-Token": readCookie("ta_csrf"), "X-Idempotency-Key": `web-${crypto.randomUUID()}` } : {}),
    },
    body: JSON.stringify(payload),
  });
  const body = await readJson<T & { detail?: string }>(response);
  if (!response.ok) throw new Error(responseError(body));
  return body;
}

async function actionWithCsrf(action: () => Promise<void>, _csrf: boolean) {
  await action();
}

async function runPkceExchange(clientId: string, redirectUri: string, scope: string) {
  const state = randomBase64Url(18);
  const verifier = randomBase64Url(48);
  const authorize = await postJson<{ redirect_uri?: string }>("/api/v1/oauth/authorize", {
    client_id: clientId,
    redirect_uri: redirectUri,
    scope,
    state,
    code_challenge: await pkceChallenge(verifier),
    code_challenge_method: "S256",
    action: "approve",
  }, true);
  const callbackUrl = new URL(authorize.redirect_uri ?? "");
  const code = callbackUrl.searchParams.get("code");
  if (!code || callbackUrl.searchParams.get("state") !== state) throw new Error("invalid callback");
  const token = await postJson<{ access_token?: string }>("/api/v1/oauth/token", {
    grant_type: "authorization_code",
    code,
    client_id: clientId,
    redirect_uri: redirectUri,
    code_verifier: verifier,
  });
  if (!token.access_token) throw new Error("missing access token");
  return token.access_token;
}

function parseScopes(scopeText: string) {
  return scopeText.split(/\s+/).map((scope) => scope.trim()).filter(Boolean);
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

function notifyAuthChanged() {
  window.dispatchEvent(new Event("track-anywhere-auth-changed"));
}
