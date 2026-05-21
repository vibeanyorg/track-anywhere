"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { readJson } from "../../lib/http";

type SessionResponse = {
  authenticated: boolean;
  identity: {
    display_name?: string | null;
    email?: string | null;
    role?: string | null;
  } | null;
};

export function CliCallback() {
  const searchParams = useSearchParams();
  const [session, setSession] = useState<SessionResponse>({ authenticated: false, identity: null });
  const [callbackUrl, setCallbackUrl] = useState("");
  const [status, setStatus] = useState("Checking");
  const [error, setError] = useState("");
  const hasCode = Boolean(searchParams.get("code"));
  const loginNext = useMemo(() => {
    const path = `/auth/callback?${searchParams.toString()}`;
    return `/auth/login?next=${encodeURIComponent(path)}`;
  }, [searchParams]);
  const displayName = session.identity?.display_name || session.identity?.email || "Signed in";

  useEffect(() => {
    if (hasCode && typeof window !== "undefined") {
      setCallbackUrl(window.location.href);
      setStatus("Authorized");
      setError("");
      return;
    }
    fetch("/api/v1/auth/session", { credentials: "include", cache: "no-store" })
      .then((response) => readJson<SessionResponse>(response))
      .then((payload: SessionResponse) => {
        setSession(payload);
        setStatus(payload.authenticated ? "Ready" : "Login required");
        setError("");
      })
      .catch(() => {
        setStatus("Offline");
        setError("Backend is not reachable.");
      });
  }, [hasCode]);

  async function authorizeCli() {
    setStatus("Authorizing");
    setError("");
    try {
      const payload = authorizationPayload(searchParams);
      const response = await fetch("/api/v1/oauth/authorize", {
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
        throw new Error(friendlyAuthError(data.detail));
      }
      setCallbackUrl(data.redirect_uri);
      setStatus("Authorized");
    } catch (error) {
      setStatus("Blocked");
      setError(error instanceof Error ? error.message : "Authorization failed.");
    }
  }

  async function copyCallback() {
    if (!callbackUrl) return;
    await navigator.clipboard.writeText(callbackUrl);
    setStatus("Copied");
  }

  return (
    <main className="auth-page cli-auth-page">
      <Link className="brand-mark cli-auth-brand" href="/">
        <span className="brand-symbol">TA</span>
        <span>Track Anywhere</span>
      </Link>

      <section className="cli-auth-panel" aria-labelledby="cli-callback-title">
        <div className="cli-auth-heading">
          <p className="eyebrow">CLI authorization</p>
          <h1 id="cli-callback-title">Authorize CLI</h1>
          <span className={`auth-state ${status === "Authorized" || status === "Ready" ? "auth-state-on" : ""}`}>{status}</span>
        </div>
        {error ? <p className="callback-error">{error}</p> : null}

        {callbackUrl ? (
          <div className="callback-output">
            <textarea id="cli-callback-url" name="cli_callback_url" className="callback-code" readOnly value={callbackUrl} aria-label="CLI callback URL" />
            <button className="primary-action cli-auth-submit" type="button" onClick={copyCallback}>
              Copy callback
            </button>
          </div>
        ) : session.authenticated ? (
          <div className="callback-output">
            <span className="identity-chip">{displayName}</span>
            <button className="primary-action cli-auth-submit" type="button" onClick={authorizeCli}>
              Authorize CLI
            </button>
          </div>
        ) : (
          <div className="auth-form-switch">
            <Link className="text-button text-button-strong" href={loginNext}>
              Log in
            </Link>
            <Link className="text-button" href={`/auth/signup?next=${encodeURIComponent(`/auth/callback?${searchParams.toString()}`)}`}>
              Register
            </Link>
          </div>
        )}
      </section>
    </main>
  );
}

function authorizationPayload(searchParams: { get(name: string): string | null }) {
  const required = ["client_id", "redirect_uri", "state", "code_challenge"];
  for (const key of required) {
    if (!searchParams.get(key)) throw new Error("Invalid CLI authorization request");
  }
  return {
    client_id: searchParams.get("client_id"),
    redirect_uri: searchParams.get("redirect_uri"),
    scope: searchParams.get("scope") || "account:read book:read ledger:read",
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

function friendlyAuthError(detail: string | undefined) {
  if (!detail) return "Authorization failed.";
  if (detail.includes("actor lacks requested scopes")) {
    return "This account cannot grant the requested CLI scopes.";
  }
  return detail.length > 140 ? `${detail.slice(0, 137)}...` : detail;
}
