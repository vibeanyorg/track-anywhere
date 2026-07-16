"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useAuth } from "../../components/auth-provider";
import { readJson } from "../../lib/http";
import {
  buildAuthorizationResponsePayload,
  canApproveScope,
  canBrowserSessionApproveOAuth,
  canSubmitAuthorizationApproval,
  defaultApprovedScopes,
  parseAuthorizationRequest,
  requiredApprovalScopes,
  scopeSelectionStatus,
  updateApprovedScopes,
  validateAuthorizationRedirect
} from "../../lib/oauth-consent.mjs";

const SCOPE_COPY: Record<string, { title: string; description: string }> = {
  "book:read": {
    title: "View books",
    description: "See the books and memberships available to your account. Turning this off also turns off book:write."
  },
  "book:write": {
    title: "Manage books",
    description: "Create or change book-level data and access settings. Requires book:read; selecting it also selects that read permission."
  },
  "ledger:read": {
    title: "View ledger",
    description: "Read accounts, balances, categories, and transactions. Turning this off also turns off ledger:write."
  },
  "ledger:write": {
    title: "Write to ledger",
    description: "Record expenses, transfers, credit-card charges, and card payments. Requires ledger:read; selecting it also selects that read permission."
  }
};

export function CliCallback() {
  const searchParams = useSearchParams();
  const { session, loading, offline } = useAuth();
  const [callbackUrl, setCallbackUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [approvedScopes, setApprovedScopes] = useState<string[]>([]);
  const [scopeStatus, setScopeStatus] = useState("");
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
  const availableScopes = session.identity?.scopes || [];
  const actorBookId = session.identity?.book_id;
  const actorCanApprove = canBrowserSessionApproveOAuth(actorBookId);
  const bookBoundActor = Boolean(actorBookId);
  const requestedScopeKey = authorization.request?.payload.scope || "";
  const requestedResourceKey = authorization.request?.resource || "";
  const availableScopeKey = availableScopes.join(" ");
  const requiredScopes = authorization.request
    ? requiredApprovalScopes(authorization.request.resource)
    : [];
  const canSubmitApproval = authorization.request
    ? canSubmitAuthorizationApproval(
        authorization.request.payload,
        approvedScopes,
        actorBookId
      )
    : false;

  useEffect(() => {
    if (hasAuthorizationResult && typeof window !== "undefined") {
      setCallbackUrl(window.location.href);
    }
  }, [hasAuthorizationResult]);

  useEffect(() => {
    setApprovedScopes(
      defaultApprovedScopes(
        authorization.request?.scopes || [],
        session.identity?.scopes || []
      )
    );
    setScopeStatus("");
  }, [requestedScopeKey, requestedResourceKey, availableScopeKey, actorBookId]);

  async function authorize(action: "approve" | "deny") {
    if (!authorization.request) return;
    if (action === "approve" && !canSubmitApproval) {
      setError(
        bookBoundActor
          ? "Sign in with your owner password before approving OAuth access."
          : "Keep every required permission selected before allowing access."
      );
      return;
    }
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
        body: JSON.stringify(
          buildAuthorizationResponsePayload(
            authorization.request.payload,
            action,
            approvedScopes
          )
        )
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

  function setScopeSelected(scope: string, selected: boolean) {
    if (!authorization.request) return;
    setError("");
    try {
      const nextScopes = updateApprovedScopes(
        authorization.request.scopes,
        availableScopes,
        approvedScopes,
        scope,
        selected,
        authorization.request.resource
      );
      setApprovedScopes(nextScopes);
      setScopeStatus(
        scopeSelectionStatus(approvedScopes, nextScopes, scope, selected)
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "This permission is unavailable.");
    }
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
            </dl>
            {bookBoundActor ? (
              <p className="callback-error" role="alert">
                This book-bound API-key session cannot authorize OAuth apps. Sign out,
                then sign in with your owner password. You can still deny this request.
              </p>
            ) : null}
            <fieldset
              className="oauth-scope-fieldset"
              aria-describedby="oauth-permission-help oauth-selection-note"
            >
              <legend>Permissions</legend>
              <div className="oauth-scope-picker">
                <p id="oauth-permission-help" className="oauth-scope-help">
                  {requiredScopes.length
                    ? "ledger:read is required for this MCP connection and cannot be turned off. Write access stays optional and requires its matching read permission."
                    : "Read access is selected by default. Write access stays optional and requires its matching read permission."}
                </p>
                {authorization.request.scopes.map((scope) => {
                  const grantable = canApproveScope(
                    authorization.request.scopes,
                    availableScopes,
                    scope
                  );
                  const copy = SCOPE_COPY[scope] || {
                    title: scope,
                    description: "Grant this requested OAuth permission."
                  };
                  const scopeId = `oauth-scope-${scope.replace(":", "-")}`;
                  const descriptionId = `${scopeId}-description`;
                  const requiredScope = requiredScopes.includes(scope);
                  const unavailable = !grantable || !actorCanApprove;
                  const locked = requiredScope && !unavailable;
                  const disabled = busy || unavailable || locked;
                  return (
                    <label
                      className={`oauth-scope-option${busy || unavailable ? " oauth-scope-option-disabled" : ""}${locked ? " oauth-scope-option-locked" : ""}`}
                      key={scope}
                      htmlFor={scopeId}
                    >
                      <input
                        id={scopeId}
                        name="approved_scopes"
                        type="checkbox"
                        value={scope}
                        checked={approvedScopes.includes(scope)}
                        disabled={disabled}
                        aria-describedby={descriptionId}
                        onChange={(event) => setScopeSelected(scope, event.target.checked)}
                      />
                      <span className="oauth-scope-copy">
                        <span className="oauth-scope-title">
                          <strong>{copy.title}</strong>
                          <code>{scope}</code>
                        </span>
                        <span id={descriptionId} className="oauth-scope-description">
                          {copy.description}
                          {requiredScope
                            ? " Required for this MCP connection and cannot be turned off."
                            : ""}
                          {!actorCanApprove
                            ? " This browser session cannot approve OAuth access."
                            : !grantable
                            ? " Your account cannot grant this permission."
                            : ""}
                        </span>
                      </span>
                    </label>
                  );
                })}
                <p
                  id="oauth-selection-note"
                  className="oauth-selection-note"
                  role="status"
                  aria-live="polite"
                  aria-atomic="true"
                >
                  {scopeStatus ||
                    (bookBoundActor
                      ? "Approval is unavailable for this book-bound session; Deny remains available."
                      : approvedScopes.length === 0
                      ? "Select at least one permission, or deny this request."
                      : !canSubmitApproval
                      ? "Keep every required permission selected, or deny this request."
                      : "Only the permissions selected here will be granted.")}
                </p>
              </div>
            </fieldset>
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
                disabled={busy || !canSubmitApproval}
              >
                {busy ? "Responding…" : "Allow access"}
              </button>
            </div>
          </div>
        ) : (
          <div className="auth-form-switch">
            <Link className="text-button text-button-strong" href={loginNext}>
              Sign in to continue
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
  if (detail.includes("requested OAuth scope is not available")) {
    return "Your account can't grant this app what it's asking for.";
  }
  return detail.length > 140 ? `${detail.slice(0, 137)}…` : detail;
}
