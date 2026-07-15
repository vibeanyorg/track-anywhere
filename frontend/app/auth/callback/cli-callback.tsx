"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useAuth } from "../../components/auth-provider";
import { readJson } from "../../lib/http";
import {
  parseAuthorizationRequest,
  validateAuthorizationRedirect
} from "../../lib/oauth-consent.mjs";

export function CliCallback() {
  const searchParams = useSearchParams();
  const { session, loading, offline } = useAuth();
  const [callbackUrl, setCallbackUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const hasAuthorizationResult = Boolean(
    searchParams.get("code") || searchParams.get("error")
  );

  const authorization = useMemo(() => {
    if (hasAuthorizationResult) return { request: null, error: "" };
    try {
      return { request: parseAuthorizationRequest(searchParams), error: "" };
    } catch (err) {
      return {
        request: null,
        error: err instanceof Error ? err.message : "This authorization link is invalid."
      };
    }
  }, [hasAuthorizationResult, searchParams]);

  const loginNext = useMemo(() => {
    const path = `/auth/callback?${searchParams.toString()}`;
    return `/auth/login?next=${encodeURIComponent(path)}`;
  }, [searchParams]);

  const displayName = session.identity?.display_name || "You";

  useEffect(() => {
    if (hasAuthorizationResult && typeof window !== "undefined") {
      setCallbackUrl(window.location.href);
    }
  }, [hasAuthorizationResult]);

  async function authorize(action: "approve" | "deny") {
    if (!authorization.request) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/v2/oauth/authorize", {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": readCookie("ta_csrf")
        },
        body: JSON.stringify({ ...authorization.request.payload, action })
      });
      const data = await readJson<{
        redirect_uri?: string;
        detail?: string;
        error_description?: string;
      }>(response);
      if (!response.ok || !data.redirect_uri) {
        throw new Error(friendlyError(data.detail || data.error_description));
      }
      const redirect = validateAuthorizationRedirect(
        data.redirect_uri,
        authorization.request.payload.redirect_uri
      );
      setCallbackUrl(redirect);
      window.setTimeout(() => window.location.assign(redirect), 50);
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
          <div>
            <p className="eyebrow">OAuth authorization</p>
            <h1 id="cli-callback-title">
              {callbackUrl ? "Return to your app" : "Allow this app?"}
            </h1>
          </div>
          <p className="auth-form-subhead">
            {callbackUrl
              ? "Redirecting now. If that does not work, copy the URL below."
              : session.authenticated
              ? "Review exactly what the app is asking to access."
              : "Sign in before approving or denying this request."}
          </p>
          {offline ? <span className="auth-state">Can't reach the server.</span> : null}
          {authorization.error || error ? (
            <p className="callback-error" role="alert">
              {authorization.error || error}
            </p>
          ) : null}
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
        ) : session.authenticated && authorization.request ? (
          <div className="oauth-consent">
            <dl className="oauth-consent-details">
              <div>
                <dt>Client ID</dt>
                <dd>{authorization.request.clientId}</dd>
              </div>
              <div>
                <dt>Resource</dt>
                <dd>{authorization.request.resource}</dd>
              </div>
              <div>
                <dt>Redirect host</dt>
                <dd>{authorization.request.redirectHost}</dd>
              </div>
              <div>
                <dt>Permissions</dt>
                <dd className="oauth-scope-list">
                  {authorization.request.scopes.map((scope) => (
                    <code key={scope}>{scope}</code>
                  ))}
                </dd>
              </div>
            </dl>
            <div className="oauth-consent-identity">
              <span>Authorizing as</span>
              <strong>{displayName}</strong>
            </div>
            <div className="oauth-consent-actions">
              <button
                className="secondary-action cli-auth-submit"
                type="button"
                onClick={() => authorize("deny")}
                disabled={busy}
              >
                Deny
              </button>
              <button
                className="primary-action cli-auth-submit"
                type="button"
                onClick={() => authorize("approve")}
                disabled={busy}
              >
                {busy ? "Responding…" : "Allow access"}
              </button>
            </div>
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
