// SPDX-License-Identifier: AGPL-3.0-only
import { useState } from "react";
import { Link, NavLink, Navigate, Outlet, useLocation, useNavigate } from "react-router";

import { describeError } from "../api/client";
import { auth } from "../api/endpoints";
import { useAuth } from "../auth/AuthContext";
import { useOfflineSync } from "../offline/useOfflineSync";
import { BrandMark } from "./BrandMark";
import { SiteFooter } from "./SiteFooter";
import { ErrorText, Loading } from "./ui";

function VerifyBanner() {
  const [state, setState] = useState<"idle" | "sent" | "error">("idle");
  return (
    <div className="banner" role="status">
      <span>Confirm your email address to keep your account recoverable.</span>{" "}
      {state === "sent" ? (
        <span>Check your inbox for the link.</span>
      ) : (
        <button
          type="button"
          className="link-button"
          onClick={() => {
            auth.resendVerification().then(
              () => {
                setState("sent");
              },
              () => {
                setState("error");
              },
            );
          }}
        >
          Send the link again
        </button>
      )}
      {state === "error" ? <span> Could not send it. Try again later.</span> : null}
    </div>
  );
}

/** The signed-in frame: header, navigation, connectivity and the page. */
export function Layout() {
  const { user, loading, signOut } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const sync = useOfflineSync(user?.id ?? null);
  const [error, setError] = useState<string | null>(null);

  if (loading) {
    return <Loading />;
  }
  if (user === null) {
    // A page whose fragment carries a token (a shared-deck link) passes it on
    // in the login page's fragment too, which is never sent to a server.
    const next = encodeURIComponent(`${location.pathname}${location.search}${location.hash}`);
    return <Navigate to={location.hash === "" ? `/login?next=${next}` : `/login#next=${next}`} replace />;
  }

  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="site-header">
        <Link to="/" className="brand">
          <BrandMark />
          Flash-Cards
        </Link>
        <nav aria-label="Main">
          <NavLink to="/" end>
            Decks
          </NavLink>
          {user.role === "admin" ? <NavLink to="/cards">Browse</NavLink> : null}
          <NavLink to="/goals">Goals</NavLink>
          <NavLink to="/progress">Progress</NavLink>
          <NavLink to="/account">Account</NavLink>
          {user.role === "admin" ? <NavLink to="/admin">Admin</NavLink> : null}
        </nav>
        <button
          type="button"
          className="link-button"
          onClick={() => {
            setError(null);
            signOut().then(
              () => {
                void navigate("/login");
              },
              (failure: unknown) => {
                setError(describeError(failure));
              },
            );
          }}
        >
          Sign out
        </button>
      </header>
      {!sync.online || sync.waiting > 0 ? (
        <div className="banner" role="status" aria-live="polite">
          {sync.online ? "Back online." : "You are offline. Reviews are saved on this device."}{" "}
          {sync.waiting > 0
            ? `${sync.waiting} ${sync.waiting === 1 ? "answer is" : "answers are"} waiting to sync.`
            : null}
          {sync.online && sync.waiting > 0 ? (
            <>
              {" "}
              <button
                type="button"
                className="link-button"
                onClick={() => {
                  void sync.syncNow();
                }}
              >
                Sync now
              </button>
            </>
          ) : null}
        </div>
      ) : null}
      {user.email_verified ? null : <VerifyBanner />}
      <main id="main" tabIndex={-1}>
        <ErrorText message={error} />
        <Outlet />
      </main>
      <SiteFooter />
    </>
  );
}
