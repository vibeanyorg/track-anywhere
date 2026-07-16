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
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [setupKey, setSetupKey] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const nextPath = useMemo(() => safeNext(searchParams.get("next")), [searchParams]);

  async function submitCredentials(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (password.length < 12) {
      setError("Password must be at least 12 characters.");
      return;
    }
    if (mode === "signup" && password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    const endpoint = mode === "signup" ? "/api/v2/auth/signup" : "/api/v2/auth/session/password";
    const payload =
      mode === "signup"
        ? { display_name: displayName.trim(), email: email.trim(), password, setup_key: setupKey.trim() }
        : { email: email.trim(), password };

    await createSession(endpoint, payload, "Those account details didn't work.");
  }

  async function submitApiKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const key = apiKey.trim();
    if (!key) {
      setError("Paste your API key first.");
      return;
    }

    await createSession(
      "/api/v2/auth/session/api-key",
      { api_key: key },
      "That API key didn't work."
    );
  }

  async function createSession(endpoint: string, body: object, fallback: string) {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      const payload = await readJson<{ authenticated?: boolean; detail?: string }>(response);
      if (!response.ok || !payload.authenticated) {
        throw new Error(responseError(payload, fallback));
      }
      setPassword("");
      setConfirmPassword("");
      setSetupKey("");
      setApiKey("");
      notifyChanged();
      router.push(nextPath);
    } catch (err) {
      setError(err instanceof Error ? err.message : fallback);
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
          <p className="eyebrow">{mode === "signup" ? "Private instance setup" : "Your account"}</p>
          <h1 id="auth-form-title">{mode === "signup" ? "Create the owner account" : "Welcome back"}</h1>
          <p className="auth-form-subhead">
            {mode === "signup"
              ? "Use the setup key created during deployment to claim this private ledger. Once an owner exists, signup stays closed."
              : "Sign in with the email and password you used to set up this ledger."}
          </p>
          {error ? (
            <p className="callback-error" role="alert">
              {error}
            </p>
          ) : null}
        </div>

        <form className="auth-form" onSubmit={submitCredentials}>
          {mode === "signup" ? (
            <>
              <label className="auth-label" htmlFor="display-name">
                Display name
                <input
                  id="display-name"
                  name="display_name"
                  className="auth-input auth-input-wide"
                  autoComplete="name"
                  type="text"
                  maxLength={120}
                  required
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                />
              </label>
              <label className="auth-label" htmlFor="setup-key">
                Setup key
                <input
                  id="setup-key"
                  name="setup_key"
                  className="auth-input auth-input-wide"
                  autoComplete="off"
                  type="password"
                  maxLength={512}
                  required
                  value={setupKey}
                  onChange={(event) => setSetupKey(event.target.value)}
                  placeholder="ta_…"
                />
                <small className="auth-field-help">
                  Use the personal setup key generated for this deployment.
                </small>
              </label>
            </>
          ) : null}
          <label className="auth-label" htmlFor="email">
            Email
            <input
              id="email"
              name="email"
              className="auth-input auth-input-wide"
              autoComplete="email"
              type="email"
              maxLength={254}
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </label>
          <label className="auth-label" htmlFor="password">
            Password
            <input
              id="password"
              name="password"
              className="auth-input auth-input-wide"
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
              type="password"
              minLength={12}
              maxLength={128}
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              aria-describedby="password-help"
            />
            <small id="password-help" className="auth-field-help">
              Use 12 to 128 characters.
            </small>
          </label>
          {mode === "signup" ? (
            <label className="auth-label" htmlFor="confirm-password">
              Confirm password
              <input
                id="confirm-password"
                name="confirm_password"
                className="auth-input auth-input-wide"
                autoComplete="new-password"
                type="password"
                minLength={12}
                maxLength={128}
                required
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
              />
            </label>
          ) : null}
          <button className="primary-action auth-form-submit" type="submit" disabled={busy}>
            {busy ? "Working…" : mode === "signup" ? "Create owner account" : "Sign in"}
          </button>
        </form>

        {mode === "login" ? (
          <details className="api-key-login">
            <summary>Use an API key instead</summary>
            <p className="auth-field-help">
              Existing personal API keys still work. The key is exchanged for a browser session.
            </p>
            <form className="auth-form" onSubmit={submitApiKey}>
              <label className="auth-label" htmlFor="api-key">
                API key
                <input
                  id="api-key"
                  name="api_key"
                  className="auth-input auth-input-wide"
                  autoComplete="off"
                  type="password"
                  maxLength={512}
                  required
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder="ta_…"
                />
              </label>
              <button className="secondary-action auth-form-submit" type="submit" disabled={busy}>
                {busy ? "Signing in…" : "Sign in with API key"}
              </button>
            </form>
          </details>
        ) : null}

        <div className="auth-form-switch">
          {mode === "signup" ? (
            <Link className="text-button text-button-strong" href={`/auth/login?next=${encodeURIComponent(nextPath)}`}>
              Already set up? Sign in
            </Link>
          ) : (
            <Link className="text-button" href={`/auth/signup?next=${encodeURIComponent(nextPath)}`}>
              Create account
            </Link>
          )}
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
