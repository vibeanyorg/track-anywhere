"use client";

import { useEffect, useMemo, useState } from "react";
import { accountUrl } from "./auth-links";
import { readJson, responseError } from "../lib/http";

type Identity = {
  provider?: string;
  email?: string | null;
  name?: string | null;
  display_name?: string | null;
  role?: string | null;
};

type SessionResponse = {
  authenticated: boolean;
  identity: Identity | null;
  csrf_token?: string;
};

type TokenResponse = {
  access_token?: string;
  token_type?: string;
  expires_in?: number;
  scope?: string;
};

const sessionEndpoint = "/api/v1/auth/session";
const apiKeySessionEndpoint = "/api/v1/auth/session/api-key";
const authorizeEndpoint = "/api/v1/oauth/authorize";
const tokenEndpoint = "/api/v1/oauth/token";
const defaultScope = "account:read book:read ledger:read";
const defaultClientId = "track-anywhere-web";

export function AuthHeader() {
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isBusy, setIsBusy] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [status, setStatus] = useState<string>("Connecting");

  const displayName = useMemo(() => {
    const identity = session?.identity;
    return identity?.display_name || identity?.name || identity?.email || "Signed in";
  }, [session]);

  async function refreshAuth(nextStatus?: string) {
    setIsLoading(true);
    try {
      const response = await fetch(sessionEndpoint, { credentials: "include", cache: "no-store" });
      const nextSession = await readJson<SessionResponse>(response);
      setSession(nextSession);
      setStatus(nextStatus ?? (nextSession.authenticated ? "Session active" : "Ready"));
    } catch {
      setSession({ authenticated: false, identity: null });
      setStatus("Backend offline");
    } finally {
      setIsLoading(false);
    }
  }

  async function createSessionFromKey(key: string, nextStatus: string) {
    const response = await fetch(apiKeySessionEndpoint, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: key })
    });
    const payload = await readJson<{ detail?: string }>(response);
    if (!response.ok) {
      throw new Error(responseError(payload, "key rejected"));
    }
    setApiKey("");
    await refreshAuth(nextStatus);
    notifyAuthChanged();
  }

  async function loginWithApiKey() {
    const key = apiKey.trim();
    if (!key) {
      setStatus("API key required");
      return;
    }
    setIsBusy(true);
    setStatus("Checking key");
    try {
      await createSessionFromKey(key, "API key accepted");
    } catch {
      setStatus("API key rejected");
    } finally {
      setIsBusy(false);
    }
  }

  async function startLocalSession() {
    setIsBusy(true);
    setStatus("Opening local session");
    try {
      const response = await fetch("/api/v1/session/dev-local", {
        method: "POST",
        credentials: "include"
      });
      const payload = await readJson<{ detail?: string }>(response);
      if (!response.ok) {
        throw new Error(responseError(payload, "local session failed"));
      }
      await refreshAuth();
      notifyAuthChanged();
    } catch {
      setStatus("Local session unavailable");
    } finally {
      setIsBusy(false);
    }
  }

  async function exchangePlatformToken() {
    if (!session?.authenticated) {
      setStatus("Session required");
      return;
    }

    setIsBusy(true);
    setStatus("Authorizing");
    try {
      const redirectUri = `${window.location.origin}/auth/callback`;
      const state = randomBase64Url(18);
      const verifier = randomBase64Url(48);
      const challenge = await pkceChallenge(verifier);
      const csrfToken = readCookie("ta_csrf");
      const authorizeResponse = await fetch(authorizeEndpoint, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {})
        },
        body: JSON.stringify({
          client_id: defaultClientId,
          redirect_uri: redirectUri,
          scope: defaultScope,
          state,
          code_challenge: challenge,
          code_challenge_method: "S256",
          action: "approve"
        })
      });
      const authorizePayload = await readJson<{ redirect_uri?: string; detail?: string }>(authorizeResponse);
      if (!authorizeResponse.ok || !authorizePayload.redirect_uri) {
        throw new Error(responseError(authorizePayload, "authorization failed"));
      }

      const callbackUrl = new URL(authorizePayload.redirect_uri);
      const code = callbackUrl.searchParams.get("code");
      if (!code || callbackUrl.searchParams.get("state") !== state) {
        throw new Error("callback rejected");
      }

      setStatus("Exchanging code");
      const tokenResponse = await fetch(tokenEndpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          grant_type: "authorization_code",
          code,
          client_id: defaultClientId,
          redirect_uri: redirectUri,
          code_verifier: verifier
        })
      });
      const tokenPayload = await readJson<TokenResponse & { detail?: string }>(tokenResponse);
      if (!tokenResponse.ok || !tokenPayload.access_token) {
        throw new Error(responseError(tokenPayload, "token exchange failed"));
      }

      await createSessionFromKey(tokenPayload.access_token, "Platform token active");
    } catch {
      setStatus("Exchange failed");
    } finally {
      setIsBusy(false);
    }
  }

  async function logout() {
    setIsBusy(true);
    setStatus("Signing out");
    try {
      await fetch("/api/v1/auth/logout", {
        method: "POST",
        credentials: "include"
      });
    } finally {
      await refreshAuth();
      notifyAuthChanged();
      setIsBusy(false);
    }
  }

  useEffect(() => {
    void refreshAuth();
    const handleAuthChanged = () => {
      void refreshAuth();
    };
    window.addEventListener("track-anywhere-auth-changed", handleAuthChanged);
    return () => window.removeEventListener("track-anywhere-auth-changed", handleAuthChanged);
  }, []);

  return (
    <header id="auth" className="site-header">
      <a className="brand-mark" href="/" aria-label="Track Anywhere home">
        <span className="brand-symbol">TA</span>
        <span>Track Anywhere</span>
      </a>

      <div className="auth-panel" aria-live="polite">
        <span className={`auth-state ${session?.authenticated ? "auth-state-on" : ""}`}>{isLoading ? "Checking" : status}</span>
        {session?.authenticated ? (
          <>
            <span className="identity-chip">
              {displayName}
              {session.identity?.role ? <small>{session.identity.role}</small> : null}
            </span>
            <button className="text-button text-button-strong" type="button" onClick={exchangePlatformToken} disabled={isBusy}>
              Exchange OAuth
            </button>
            <button className="text-button" type="button" onClick={logout} disabled={isBusy}>
              Log out
            </button>
          </>
        ) : (
          <>
            <a className="text-button" href={accountUrl("login")}>
              Log in
            </a>
            <a className="text-button" href={accountUrl("signup")}>
              Register
            </a>
            <input
              className="auth-input"
              aria-label="API key"
              autoComplete="off"
              id="header-api-key"
              name="header_api_key"
              placeholder="ta_ API key"
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  void loginWithApiKey();
                }
              }}
            />
            <button className="text-button text-button-strong" type="button" onClick={loginWithApiKey} disabled={isBusy}>
              Connect
            </button>
            <button className="text-button" type="button" onClick={startLocalSession} disabled={isBusy}>
              Local
            </button>
          </>
        )}
      </div>
    </header>
  );
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

function notifyAuthChanged() {
  window.dispatchEvent(new Event("track-anywhere-auth-changed"));
}
