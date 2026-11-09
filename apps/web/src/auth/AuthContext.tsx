// SPDX-License-Identifier: AGPL-3.0-only
// The signed-in user. The session itself is an HttpOnly cookie the client never
// sees; GET /auth/me says who it belongs to.

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { ApiError, NetworkError, onUnauthorized } from "../api/client";
import { auth } from "../api/endpoints";
import type { User } from "../api/types";
import { forgetAllJobs } from "../lib/recentJobs";
import { clearStudyDataCache } from "../offline/apiCache";

// Who was last signed in on this device, so the app can open for offline study
// when /auth/me cannot be reached. It holds no secret: the cookie does that.
const LAST_USER_KEY = "flashcards.lastUser";

function rememberUser(user: User | null): void {
  try {
    if (user === null) {
      localStorage.removeItem(LAST_USER_KEY);
    } else {
      localStorage.setItem(LAST_USER_KEY, JSON.stringify(user));
    }
  } catch {
    // Storage can be unavailable (private mode); offline start is then skipped.
  }
}

function lastUser(): User | null {
  try {
    const raw = localStorage.getItem(LAST_USER_KEY);
    return raw === null ? null : (JSON.parse(raw) as User);
  } catch {
    return null;
  }
}

type Session = { kind: "user"; user: User } | { kind: "offline"; user: User | null } | { kind: "signed-out" };

/** Who the session cookie belongs to; offline, who was last signed in here. */
async function currentSession(): Promise<Session> {
  try {
    return { kind: "user", user: await auth.me() };
  } catch (error) {
    return error instanceof NetworkError ? { kind: "offline", user: lastUser() } : { kind: "signed-out" };
  }
}

export interface AuthState {
  loading: boolean;
  user: User | null;
  signedIn: (user: User) => void;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [user, setUser] = useState<User | null>(null);

  const signedIn = useCallback((next: User) => {
    rememberUser(next);
    setUser(next);
  }, []);

  const signedOut = useCallback(() => {
    rememberUser(null);
    forgetAllJobs();
    setUser(null);
    void clearStudyDataCache();
  }, []);

  const apply = useCallback(
    (result: Session) => {
      if (result.kind === "user") {
        signedIn(result.user);
      } else if (result.kind === "offline") {
        setUser(result.user);
      } else {
        signedOut();
      }
      setLoading(false);
    },
    [signedIn, signedOut],
  );

  const refresh = useCallback(async () => {
    apply(await currentSession());
  }, [apply]);

  const signOut = useCallback(async () => {
    try {
      await auth.logout();
    } catch (error) {
      // An expired session is already signed out; anything else is reported.
      if (!(error instanceof ApiError && error.status === 401)) {
        throw error;
      }
    }
    signedOut();
  }, [signedOut]);

  useEffect(() => {
    onUnauthorized(signedOut);
    let active = true;
    void currentSession().then((result) => {
      if (active) {
        apply(result);
      }
    });
    return () => {
      active = false;
      onUnauthorized(null);
    };
  }, [apply, signedOut]);

  const value = useMemo(
    () => ({ loading, user, signedIn, refresh, signOut }),
    [loading, user, signedIn, refresh, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (value === null) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return value;
}
