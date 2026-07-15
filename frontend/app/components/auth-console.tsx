"use client";

import { useEffect, useMemo, useState } from "react";
import { useAuth } from "./auth-provider";
import { readJson, responseError } from "../lib/http";
import { oauthRedirectUri } from "../lib/public-origin.mjs";

const defaultScope = "book:read ledger:read";

type OAuthClient = {
  client_id: string;
  client_name: string;
  redirect_uris: string[];
  scope: string;
};

export function AuthConsole() {
  const { session } = useAuth();
  const [clientName, setClientName] = useState("Local MCP Client");
  const [redirectUri, setRedirectUri] = useState("");
  const [clientScope, setClientScope] = useState(defaultScope);
  const [client, setClient] = useState<OAuthClient | null>(null);
  const [connectedToken, setConnectedToken] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const displayName = useMemo(() => {
    return session.identity?.display_name || "You";
  }, [session]);

  useEffect(() => {
    setRedirectUri(oauthRedirectUri(window.location.origin));
  }, []);

  if (!session.authenticated) {
    return null;
  }

  async function registerApp() {
    await runBusy(async () => {
      const registered = await postJson<OAuthClient>("/api/v2/oauth/register", {
        client_name: clientName,
        redirect_uris: [redirectUri],
        scope: clientScope,
        token_endpoint_auth_method: "none"
      });
      setClient(registered);
      setConnectedToken("");
      setMessage("App registered for this browser session.");
    });
  }

  async function connectApp() {
    if (!client) {
      setMessage("Register the app first.");
      return;
    }
    await runBusy(async () => {
      const token = await runPkceExchange(
        client.client_id,
        client.redirect_uris[0],
        client.scope
      );
      setConnectedToken(token);
      setMessage("Access token issued.");
    });
  }

  async function revokeToken() {
    if (!connectedToken) return;
    await runBusy(async () => {
      await postJson<{ revoked: boolean }>("/api/v2/oauth/revoke", {
        token: connectedToken
      });
      setConnectedToken("");
      setMessage("Access token revoked.");
    });
  }

  async function copyToken() {
    if (!connectedToken) return;
    await navigator.clipboard.writeText(connectedToken);
    setMessage("Token copied.");
  }

  async function runBusy(action: () => Promise<void>) {
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
    <section className="auth-console-band" aria-label="Connected apps">
      <div className="auth-console-shell">
        <div className="auth-console-heading">
          <p className="eyebrow">Account</p>
          <h2>OAuth apps</h2>
          <p className="console-helper">
            Signed in as {displayName}. API key creation and revocation are administered outside this V2 frontend.
          </p>
          {message ? <span className="auth-state auth-state-on">{message}</span> : null}
        </div>

        <div className="auth-console-panel">
          <div className="console-panel-head">
            <h3>PKCE connection</h3>
            <div>
              <button
                className="text-button"
                type="button"
                onClick={registerApp}
                disabled={busy || !redirectUri}
              >
                Register app
              </button>
              <button
                className="text-button text-button-strong"
                type="button"
                onClick={connectApp}
                disabled={busy || !client}
              >
                Connect
              </button>
            </div>
          </div>
          <p className="console-helper">Register a public OAuth client, then authorize it with PKCE.</p>
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
              placeholder="Redirect URI"
            />
            <input
              id="oauth-client-scope"
              name="oauth_client_scope"
              className="auth-input auth-input-wide"
              value={clientScope}
              onChange={(event) => setClientScope(event.target.value)}
              aria-label="What it can do"
              placeholder="OAuth scopes"
            />
          </div>
          {client ? (
            <div className="console-list">
              <div className="console-row">
                <div>
                  <strong>{client.client_name}</strong>
                  <p>{client.client_id}</p>
                  <small>{client.scope}</small>
                </div>
              </div>
            </div>
          ) : null}
          {connectedToken ? (
            <div className="secret-output">
              <span>Access token</span>
              <code>{connectedToken}</code>
              <button className="text-button" type="button" onClick={copyToken}>
                Copy
              </button>
              <button className="text-button" type="button" onClick={revokeToken} disabled={busy}>
                Revoke
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}

async function postJson<T>(url: string, payload: object, csrf = false): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(csrf ? { "X-CSRF-Token": readCookie("ta_csrf") } : {})
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
  const resource = `${window.location.origin}/api/v2`;
  const authorize = await postJson<{ redirect_uri?: string }>(
    "/api/v2/oauth/authorize",
    {
      response_type: "code",
      client_id: clientId,
      redirect_uri: redirectUri,
      scope,
      state,
      code_challenge: await pkceChallenge(verifier),
      code_challenge_method: "S256",
      resource,
      action: "approve"
    },
    true
  );
  const callbackUrl = new URL(authorize.redirect_uri ?? "");
  const code = callbackUrl.searchParams.get("code");
  if (!code || callbackUrl.searchParams.get("state") !== state) {
    throw new Error("The app didn't respond correctly.");
  }
  const token = await postJson<{ access_token?: string }>("/api/v2/oauth/token", {
    grant_type: "authorization_code",
    code,
    client_id: clientId,
    redirect_uri: redirectUri,
    code_verifier: verifier,
    resource
  });
  if (!token.access_token) throw new Error("No access token received.");
  return token.access_token;
}

function readCookie(name: string) {
  const value = document.cookie
    .split("; ")
    .find((item) => item.startsWith(`${name}=`))
    ?.split("=")[1];
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
