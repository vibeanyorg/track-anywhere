"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { readJson } from "../lib/http";

type Identity = {
  user_id: string;
  display_name: string;
  subject_type: "human" | "machine";
  auth_kind: string;
  book_id: string | null;
  scopes: string[];
};

type SessionResponse = {
  authenticated: boolean;
  identity: Identity | null;
};

type AuthContextValue = {
  session: SessionResponse;
  loading: boolean;
  offline: boolean;
  refresh: () => Promise<void>;
  notifyChanged: () => void;
};

const initialSession: SessionResponse = { authenticated: false, identity: null };

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<SessionResponse>(initialSession);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const inflightRef = useRef<Promise<void> | null>(null);

  const refresh = useCallback(async () => {
    if (inflightRef.current) return inflightRef.current;
    const task = (async () => {
      setLoading(true);
      try {
        const response = await fetch("/api/v2/auth/session", {
          credentials: "include",
          cache: "no-store"
        });
        const next = await readJson<SessionResponse>(response);
        if (!response.ok) throw new Error("Session request failed");
        setSession(next);
        setOffline(false);
      } catch {
        setSession(initialSession);
        setOffline(true);
      } finally {
        setLoading(false);
        inflightRef.current = null;
      }
    })();
    inflightRef.current = task;
    return task;
  }, []);

  const notifyChanged = useCallback(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = useMemo<AuthContextValue>(
    () => ({ session, loading, offline, refresh, notifyChanged }),
    [session, loading, offline, refresh, notifyChanged]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
