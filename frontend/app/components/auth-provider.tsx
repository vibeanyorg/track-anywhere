"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { readJson } from "../lib/http";

type Identity = {
  provider?: string;
  email?: string | null;
  name?: string | null;
  display_name?: string | null;
  role?: string | null;
};

type SessionResponse = {
  authenticated: boolean;
  identity: Identity | null;
  csrf_token?: string;
};

type CredentialSummary = {
  credential_id: string;
  key_prefix: string;
  scopes: string[];
  issued_at: string;
  expires_at: string;
  revoked_at: string | null;
  active: boolean;
};

type OAuthClientSummary = {
  client_id: string;
  client_name: string;
  redirect_uris: string[];
  scope: string;
  client_uri?: string | null;
};

type AuthContextValue = {
  session: SessionResponse;
  credentials: CredentialSummary[];
  clients: OAuthClientSummary[];
  loading: boolean;
  offline: boolean;
  refresh: () => Promise<void>;
  refreshCredentials: () => Promise<void>;
  refreshClients: () => Promise<void>;
  notifyChanged: () => void;
};

const initialSession: SessionResponse = { authenticated: false, identity: null };

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<SessionResponse>(initialSession);
  const [credentials, setCredentials] = useState<CredentialSummary[]>([]);
  const [clients, setClients] = useState<OAuthClientSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const inflightRef = useRef<Promise<void> | null>(null);

  const refreshCredentials = useCallback(async () => {
    try {
      const response = await fetch("/api/v1/credentials", { credentials: "include" });
      if (response.ok) {
        const payload = await readJson<{ credentials?: CredentialSummary[] }>(response);
        setCredentials(payload.credentials ?? []);
      }
    } catch {
      // silent; refresh() handles offline state
    }
  }, []);

  const refreshClients = useCallback(async () => {
    try {
      const response = await fetch("/api/v1/oauth/clients", { credentials: "include" });
      if (response.ok) {
        const payload = await readJson<{ clients?: OAuthClientSummary[] }>(response);
        setClients(payload.clients ?? []);
      }
    } catch {
      // silent
    }
  }, []);

  const refresh = useCallback(async () => {
    if (inflightRef.current) return inflightRef.current;
    const task = (async () => {
      setLoading(true);
      try {
        const response = await fetch("/api/v1/auth/session", { credentials: "include", cache: "no-store" });
        const next = await readJson<SessionResponse>(response);
        setSession(next);
        setOffline(false);
        if (next.authenticated) {
          await Promise.all([refreshCredentials(), refreshClients()]);
        } else {
          setCredentials([]);
          setClients([]);
        }
      } catch {
        setSession(initialSession);
        setCredentials([]);
        setClients([]);
        setOffline(true);
      } finally {
        setLoading(false);
        inflightRef.current = null;
      }
    })();
    inflightRef.current = task;
    return task;
  }, [refreshCredentials, refreshClients]);

  const notifyChanged = useCallback(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = useMemo<AuthContextValue>(
    () => ({ session, credentials, clients, loading, offline, refresh, refreshCredentials, refreshClients, notifyChanged }),
    [session, credentials, clients, loading, offline, refresh, refreshCredentials, refreshClients, notifyChanged]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
