"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useAuth } from "../../components/auth-provider";
import { readJson } from "../../lib/http";

export function CliCallback() {
  const searchParams = useSearchParams();
  const { session, loading, offline } = useAuth();
  const [callbackUrl, setCallbackUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const hasCode = Boolean(searchParams.get("code"));

  const loginNext = useMemo(() => {
    const path = `/auth/callback?${searchParams.toString()}`;
    return `/auth/login?next=${encodeURIComponent(path)}`;
  }, [searchParams]);

  const displayName = session.identity?.display_name || "You";

  useEffect(() => {
    if (hasCode && typeof window !== "undefined") {
      setCallbackUrl(window.location.href);
    }
  }, [hasCode]);

  async function connect() {
    setBusy(true);
    setError("");
    try {
      const payload = buildAuthorizationPayload(searchParams);
      const response = await fetch("/api/v2/oauth/authorize", {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": readCookie("ta_csrf")
        },
        body: JSON.stringify(payload)
      });
      const data = await readJson<{ redirect_uri?: string; detail?: string }>(response);
      if (!response.ok || !data.redirect_uri) {
        throw new Error(friendlyError(data.detail));
      }
      setCallbackUrl(data.redirect_uri);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  async function copyCallback() {
    if (!callbackUrl) return;
    await navigator.clipboard.writeText(callbackUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <main className="auth-page cli-auth-page">
      <Link className="brand-mark cli-auth-brand" href="/">
        <span className="brand-symbol">TA</span>
        <span>Track Anywhere</span>
      </Link>

      <section className="cli-auth-panel" aria-labelledby="cli-callback-title">
        <div className="cli-auth-heading">
          <h1 id="cli-callback-title">Connect your command line</h1>
          <p className="auth-form-subhead">
            {callbackUrl
              ? "Copy this and paste it back where you started."
              : session.authenticated
              ? "We'll give you a code to paste back."
              : "Sign in first, then we'll hand off."}
          </p>
          {offline ? <span className="auth-state">Can't reach the server.</span> : null}
          {error ? <span className="auth-state">{error}</span> : null}
        </div>

        {callbackUrl ? (
          <div className="callback-output">
            <textarea
              id="cli-callback-url"
              name="cli_callback_url"
              className="callback-code"
              readOnly
              value={callbackUrl}
              aria-label="Code to copy back"
            />
            <button className="primary-action cli-auth-submit" type="button" onClick={copyCallback}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        ) : loading ? (
          <p className="console-empty">Loading…</p>
        ) : session.authenticated ? (
          <div className="callback-output">
            <span className="identity-chip">{displayName}</span>
            <button className="primary-action cli-auth-submit" type="button" onClick={connect} disabled={busy}>
              {busy ? "Connecting…" : "Connect"}
            </button>
          </div>
        ) : (
          <div className="auth-form-switch">
            <Link className="text-button text-button-strong" href={loginNext}>
              Sign in with an API key
            </Link>
          </div>
        )}
      </section>
    </main>
  );
}

function buildAuthorizationPayload(searchParams: { get(name: string): string | null }) {
  const required = ["client_id", "redirect_uri", "state", "code_challenge"];
  for (const key of required) {
    if (!searchParams.get(key)) throw new Error("This link looks incomplete. Start over from the command line.");
  }
  return {
    client_id: searchParams.get("client_id"),
    redirect_uri: searchParams.get("redirect_uri"),
    scope: searchParams.get("scope") || "book:read ledger:read",
    state: searchParams.get("state"),
    code_challenge: searchParams.get("code_challenge"),
    code_challenge_method: "S256",
    action: "approve"
  };
}

function readCookie(name: string) {
  const value = document.cookie
    .split("; ")
    .find((item) => item.startsWith(`${name}=`))
    ?.split("=")[1];
  return value ? decodeURIComponent(value) : "";
}

function friendlyError(detail: string | undefined) {
  if (!detail) return "Something went wrong.";
  if (detail.includes("actor lacks requested scopes")) {
    return "Your account can't grant this app what it's asking for.";
  }
  return detail.length > 140 ? `${detail.slice(0, 137)}…` : detail;
}
