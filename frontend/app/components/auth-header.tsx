"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useAuth } from "./auth-provider";
import { readJson, responseError } from "../lib/http";
import { accountUrl } from "./auth-links";

export function AuthHeader() {
  const { session, loading, offline, notifyChanged } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const displayName = useMemo(() => {
    const identity = session.identity;
    return identity?.display_name || "You";
  }, [session]);

  async function signOut() {
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/v2/auth/logout", {
        method: "POST",
        credentials: "include",
        headers: { "X-CSRF-Token": readCookie("ta_csrf") }
      });
      if (!response.ok) {
        const payload = await readJson<{ detail?: string }>(response);
        throw new Error(responseError(payload, "Couldn't sign out."));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't sign out.");
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
            <span className="identity-chip">{displayName}</span>
            <button className="text-button" type="button" onClick={signOut} disabled={busy}>
              Sign out
            </button>
          </>
        ) : loading ? (
          <span className="auth-state">Loading…</span>
        ) : (
          <>
            <Link className="text-button" href={accountUrl("login")}>
              Sign in
            </Link>
            <Link className="text-button text-button-strong" href={accountUrl("signup")}>
              Create account
            </Link>
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
