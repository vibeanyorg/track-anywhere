"use client";

import { useMemo, useState } from "react";
import { useAuth } from "./auth-provider";
import { accountUrl } from "./auth-links";
import { readJson, responseError } from "../lib/http";

export function AuthHeader() {
  const { session, loading, offline, notifyChanged } = useAuth();
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const displayName = useMemo(() => {
    const identity = session.identity;
    return identity?.display_name || identity?.name || identity?.email || "You";
  }, [session]);

  async function signInWithKey() {
    const key = apiKey.trim();
    if (!key) {
      setError("Paste your key first.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/v1/auth/session/api-key", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: key })
      });
      const payload = await readJson<{ detail?: string }>(response);
      if (!response.ok) {
        throw new Error(responseError(payload, "That key didn't work."));
      }
      setApiKey("");
      notifyChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "That key didn't work.");
    } finally {
      setBusy(false);
    }
  }

  async function tryItOut() {
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/v1/session/dev-local", {
        method: "POST",
        credentials: "include"
      });
      if (!response.ok) {
        const payload = await readJson<{ detail?: string }>(response);
        throw new Error(responseError(payload, "Couldn't open a guest session."));
      }
      notifyChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't open a guest session.");
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    setBusy(true);
    setError("");
    try {
      await fetch("/api/v1/auth/logout", { method: "POST", credentials: "include" });
    } finally {
      notifyChanged();
      setBusy(false);
    }
  }

  return (
    <header id="auth" className="site-header">
      <a className="brand-mark" href="/" aria-label="Track Anywhere home">
        <span className="brand-symbol">TA</span>
        <span>Track Anywhere</span>
      </a>

      <div className="auth-panel" aria-live="polite">
        {offline ? <span className="auth-state">Can't reach the server</span> : null}
        {error ? <span className="auth-state">{error}</span> : null}

        {session.authenticated ? (
          <>
            <span className="identity-chip">
              {displayName}
              {session.identity?.role ? <small>{session.identity.role}</small> : null}
            </span>
            <button className="text-button" type="button" onClick={signOut} disabled={busy}>
              Sign out
            </button>
          </>
        ) : loading ? (
          <span className="auth-state">Loading…</span>
        ) : (
          <>
            <a className="text-button text-button-strong" href={accountUrl("login")}>
              Sign in
            </a>
            <a className="text-button" href={accountUrl("signup")}>
              Create account
            </a>
            <input
              className="auth-input"
              aria-label="API key"
              autoComplete="off"
              id="header-api-key"
              name="header_api_key"
              placeholder="or paste an API key"
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  void signInWithKey();
                }
              }}
            />
            <button className="text-button" type="button" onClick={signInWithKey} disabled={busy}>
              Use key
            </button>
            <button className="text-button" type="button" onClick={tryItOut} disabled={busy}>
              Try without signing up
            </button>
          </>
        )}
      </div>
    </header>
  );
}
