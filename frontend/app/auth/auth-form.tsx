"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useMemo, useState } from "react";
import { useAuth } from "../components/auth-provider";
import { readJson, responseError } from "../lib/http";

type AuthMode = "login" | "signup";

type AuthFormProps = {
  mode: AuthMode;
};

export function AuthForm({ mode }: AuthFormProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { notifyChanged } = useAuth();
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const nextPath = useMemo(() => safeNext(searchParams.get("next")), [searchParams]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const key = apiKey.trim();
    if (!key) {
      setError("Paste your API key first.");
      return;
    }

    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/v2/auth/session/api-key", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: key })
      });
      const payload = await readJson<{ authenticated?: boolean; detail?: string }>(response);
      if (!response.ok || !payload.authenticated) {
        throw new Error(responseError(payload, "That API key didn't work."));
      }
      setApiKey("");
      notifyChanged();
      router.push(nextPath);
    } catch (err) {
      setError(err instanceof Error ? err.message : "That API key didn't work.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-page">
      <Link className="brand-mark auth-page-brand" href="/">
        <span className="brand-symbol">TA</span>
        <span>Track Anywhere</span>
      </Link>

      <section className="auth-form-panel" aria-labelledby="auth-form-title">
        <div className="auth-form-heading">
          <h1 id="auth-form-title">{mode === "signup" ? "Account setup" : "Sign in with an API key"}</h1>
          <p className="auth-form-subhead">
            {mode === "signup"
              ? "Self-service account creation is not available in API V2. Ask your Book owner for an API key."
              : "Your key is exchanged for a short-lived browser session and is never stored in the browser."}
          </p>
          {error ? <span className="auth-state">{error}</span> : null}
        </div>

        {mode === "login" ? (
          <form className="auth-form" onSubmit={submit}>
            <label className="auth-label">
              API key
              <input
                id="api-key"
                name="api_key"
                className="auth-input auth-input-wide"
                autoComplete="off"
                type="password"
                required
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder="ta_…"
              />
            </label>
            <button className="primary-action auth-form-submit" type="submit" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>
        ) : null}

        <div className="auth-form-switch">
          {mode === "signup" ? (
            <Link className="text-button text-button-strong" href={`/auth/login?next=${encodeURIComponent(nextPath)}`}>
              I have an API key
            </Link>
          ) : null}
          <Link className="text-button" href={nextPath}>
            Back
          </Link>
        </div>
      </section>
    </main>
  );
}

function safeNext(value: string | null) {
  if (!value) return "/";
  if (value.startsWith("/") && !value.startsWith("//")) return value;
  try {
    const currentOrigin = typeof window === "undefined" ? "http://localhost" : window.location.origin;
    const url = new URL(value, currentOrigin);
    if (url.origin !== currentOrigin) return "/";
    return `${url.pathname}${url.search}${url.hash}` || "/";
  } catch {
    return "/";
  }
}
