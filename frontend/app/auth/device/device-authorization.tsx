"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { useAuth } from "../../components/auth-provider";
import { readJson } from "../../lib/http";
import { normalizeDeviceCode } from "../../lib/oauth-consent.mjs";

type DeviceResult = {
  status?: "approved" | "denied";
  scope?: string;
  detail?: string;
};

export function DeviceAuthorization() {
  const searchParams = useSearchParams();
  const { session, loading, offline } = useAuth();
  const [userCode, setUserCode] = useState("");
  const [result, setResult] = useState<DeviceResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const suppliedCode = searchParams.get("user_code");
    if (suppliedCode) setUserCode(normalizeDeviceCode(suppliedCode));
  }, [searchParams]);

  const loginNext = useMemo(() => {
    const code = normalizeDeviceCode(userCode);
    const path = code
      ? `/auth/device?user_code=${encodeURIComponent(code)}`
      : "/auth/device";
    return `/auth/login?next=${encodeURIComponent(path)}`;
  }, [userCode]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await respond("approve");
  }

  async function respond(action: "approve" | "deny") {
    const code = normalizeDeviceCode(userCode);
    if (!code) {
      setError("Enter the code shown by your command line or device.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/v2/auth/device", {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": readCookie("ta_csrf")
        },
        body: JSON.stringify({ user_code: code, action })
      });
      const payload = await readJson<DeviceResult>(response);
      if (!response.ok || !payload.status) {
        throw new Error(friendlyDeviceError(payload.detail));
      }
      setUserCode(code);
      setResult(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The device request could not be completed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-page cli-auth-page">
      <Link className="brand-mark cli-auth-brand" href="/">
        <span className="brand-symbol">TA</span>
        <span>Track Anywhere</span>
      </Link>

      <section className="cli-auth-panel" aria-labelledby="device-auth-title">
        <div className="cli-auth-heading">
          <div>
            <p className="eyebrow">Device authorization</p>
            <h1 id="device-auth-title">
              {result ? "Device request handled" : "Connect a device"}
            </h1>
          </div>
          <p className="auth-form-subhead">
            {result
              ? "You can close this page and return to the device."
              : "Only approve a code you just started on a device you trust."}
          </p>
          {offline ? <span className="auth-state">Can't reach the server.</span> : null}
          {error ? (
            <p className="callback-error" role="alert">
              {error}
            </p>
          ) : null}
        </div>

        {result ? (
          <div className="device-auth-result" role="status">
            <strong>{result.status === "approved" ? "Access approved" : "Access denied"}</strong>
            <code>{userCode}</code>
            {result.scope ? <p>Permissions: {result.scope}</p> : null}
          </div>
        ) : loading ? (
          <p className="console-empty">Loading…</p>
        ) : session.authenticated ? (
          <form className="device-auth-form" onSubmit={submit}>
            <label className="auth-label" htmlFor="device-user-code">
              Device code
              <input
                id="device-user-code"
                name="user_code"
                className="auth-input auth-input-wide device-code-input"
                type="text"
                autoComplete="one-time-code"
                autoCapitalize="characters"
                spellCheck={false}
                maxLength={32}
                required
                value={userCode}
                onChange={(event) => setUserCode(event.target.value.toUpperCase())}
                placeholder="ABCD-EFGH"
              />
            </label>
            <div className="oauth-consent-identity">
              <span>Authorizing as</span>
              <strong>{session.identity?.display_name || "You"}</strong>
            </div>
            <div className="oauth-consent-actions">
              <button
                className="secondary-action cli-auth-submit"
                type="button"
                disabled={busy}
                onClick={() => respond("deny")}
              >
                Deny
              </button>
              <button className="primary-action cli-auth-submit" type="submit" disabled={busy}>
                {busy ? "Responding…" : "Approve device"}
              </button>
            </div>
          </form>
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

function friendlyDeviceError(detail: string | undefined) {
  if (!detail) return "The device request could not be completed.";
  if (detail.includes("invalid or expired")) {
    return "That device code is invalid, expired, or was already used.";
  }
  return detail.length > 140 ? `${detail.slice(0, 137)}…` : detail;
}
