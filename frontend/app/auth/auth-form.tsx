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

type AuthResponse = {
  authenticated: boolean;
  identity?: {
    display_name?: string | null;
    email?: string | null;
    role?: string | null;
  } | null;
};

export function AuthForm({ mode }: AuthFormProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { notifyChanged } = useAuth();
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const nextPath = useMemo(() => safeNext(searchParams.get("next")), [searchParams]);
  const isSignup = mode === "signup";
  const alternatePath = isSignup
    ? `/auth/login?next=${encodeURIComponent(nextPath)}`
    : `/auth/signup?next=${encodeURIComponent(nextPath)}`;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSignup && password !== confirmPassword) {
      setError("Passwords don't match.");
      return;
    }

    setBusy(true);
    setError("");
    try {
      const response = await fetch(`/api/v1/auth/password/${isSignup ? "signup" : "login"}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          password,
          ...(isSignup && displayName.trim() ? { display_name: displayName.trim() } : {})
        })
      });
      const payload = await readJson<AuthResponse | { detail?: string }>(response);
      if (!response.ok || !("authenticated" in payload) || !payload.authenticated) {
        throw new Error(responseError(payload, isSignup ? "Couldn't create the account." : "Email or password didn't match."));
      }
      notifyChanged();
      router.push(nextPath);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
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
          <h1 id="auth-form-title">{isSignup ? "Create your account" : "Welcome back"}</h1>
          <p className="auth-form-subhead">
            {isSignup ? "A space for your numbers. Takes a minute." : "Sign in to pick up where you left off."}
          </p>
          {error ? <span className="auth-state">{error}</span> : null}
        </div>

        <form className="auth-form" onSubmit={submit}>
          {isSignup ? (
            <label className="auth-label">
              Your name
              <input
                id="display-name"
                name="display_name"
                className="auth-input auth-input-wide"
                autoComplete="name"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="What should we call you?"
              />
            </label>
          ) : null}
          <label className="auth-label">
            Email
            <input
              id="email"
              name="email"
              className="auth-input auth-input-wide"
              autoComplete="email"
              type="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@example.com"
            />
          </label>
          <label className="auth-label">
            Password
            <input
              id="password"
              name="password"
              className="auth-input auth-input-wide"
              autoComplete={isSignup ? "new-password" : "current-password"}
              type="password"
              minLength={8}
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={isSignup ? "At least 8 characters" : ""}
            />
          </label>
          {isSignup ? (
            <label className="auth-label">
              Confirm password
              <input
                id="confirm-password"
                name="confirm_password"
                className="auth-input auth-input-wide"
                autoComplete="new-password"
                type="password"
                minLength={8}
                required
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
              />
            </label>
          ) : null}
          <button className="primary-action auth-form-submit" type="submit" disabled={busy}>
            {busy ? (isSignup ? "Creating…" : "Signing in…") : isSignup ? "Create account" : "Sign in"}
          </button>
        </form>

        <div className="auth-form-switch">
          <Link className="text-button" href={alternatePath}>
            {isSignup ? "I already have an account" : "I'm new here — sign me up"}
          </Link>
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
    const currentOrigin = typeof window === "undefined" ? "http://localhost:3000" : window.location.origin;
    const url = new URL(value, currentOrigin);
    if (url.origin !== currentOrigin) return "/";
    return `${url.pathname}${url.search}${url.hash}` || "/";
  } catch {
    return "/";
  }
}
