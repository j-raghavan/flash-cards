// SPDX-License-Identifier: AGPL-3.0-only
// Sign-in, invite sign-up, email verification and password reset (F-AUTH-1, F-AUTH-2).

import { useEffect, useId, useRef, useState, type SubmitEvent, type ReactNode } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router";

import { describeError } from "../api/client";
import { auth } from "../api/endpoints";
import { useAuth } from "../auth/AuthContext";
import { ErrorText, Loading } from "../components/ui";
import { safeNextPath, tokenFromLocation } from "../lib/text";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useSubmit } from "../lib/useSubmit";

const MIN_PASSWORD_LENGTH = 12;

function AuthFrame({ title, children }: { title: string; children: ReactNode }) {
  useDocumentTitle(title);
  return (
    <main id="main" className="auth">
      <p className="brand">Flash-Cards</p>
      <h1>{title}</h1>
      {children}
    </main>
  );
}

function PasswordField({
  label,
  value,
  onChange,
  autoComplete,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: "current-password" | "new-password";
}) {
  const isNew = autoComplete === "new-password";
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="password"
        required
        minLength={isNew ? MIN_PASSWORD_LENGTH : undefined}
        autoComplete={autoComplete}
        value={value}
        aria-describedby={isNew ? `${id}-hint` : undefined}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
      {isNew ? (
        <small id={`${id}-hint`} className="muted">
          At least {MIN_PASSWORD_LENGTH} characters.
        </small>
      ) : null}
    </div>
  );
}

function EmailField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <label className="field">
      <span>Email</span>
      <input
        type="email"
        required
        autoComplete="email"
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
    </label>
  );
}

/** The token from the URL, then removed from the address bar and history. */
function useLinkToken(): string {
  const location = useLocation();
  const [token] = useState(() => tokenFromLocation(location.hash, location.search));
  useEffect(() => {
    if (location.hash !== "" || location.search !== "") {
      window.history.replaceState(window.history.state, "", location.pathname);
    }
  }, [location.hash, location.search, location.pathname]);
  return token;
}

export function LoginPage() {
  const { user, signedIn } = useAuth();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const submit = useSubmit();
  const location = useLocation();
  const next = safeNextPath(params.get("next") ?? new URLSearchParams(location.hash.replace(/^#/, "")).get("next"));

  if (user !== null) {
    return <Navigate to={next} replace />;
  }

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    void submit.run(async () => {
      signedIn(await auth.login(email, password));
      void navigate(next, { replace: true });
    });
  };

  return (
    <AuthFrame title="Sign in">
      <form onSubmit={onSubmit} className="stack">
        <EmailField value={email} onChange={setEmail} />
        <PasswordField label="Password" value={password} onChange={setPassword} autoComplete="current-password" />
        <ErrorText message={submit.error} />
        <button type="submit" className="button primary" disabled={submit.busy}>
          {submit.busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <p className="muted">
        <Link to="/forgot-password">Forgot your password?</Link>
      </p>
      <p className="muted">Flash-Cards is invite-only. Use the link in your invitation to create an account.</p>
    </AuthFrame>
  );
}

export function SignupPage() {
  const { signedIn } = useAuth();
  const navigate = useNavigate();
  const token = useLinkToken();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const submit = useSubmit();

  if (token === "") {
    return (
      <AuthFrame title="Create your account">
        <p>This page needs the invitation link you were sent. Open the link from your invitation email.</p>
        <p>
          <Link to="/login">Already have an account? Sign in</Link>
        </p>
      </AuthFrame>
    );
  }

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    void submit.run(async () => {
      signedIn(await auth.signup(token, email, password));
      void navigate("/", { replace: true });
    });
  };

  return (
    <AuthFrame title="Create your account">
      <form onSubmit={onSubmit} className="stack">
        <EmailField value={email} onChange={setEmail} />
        <PasswordField label="Password" value={password} onChange={setPassword} autoComplete="new-password" />
        <ErrorText message={submit.error} />
        <button type="submit" className="button primary" disabled={submit.busy}>
          {submit.busy ? "Creating…" : "Create account"}
        </button>
      </form>
    </AuthFrame>
  );
}

export function VerifyEmailPage() {
  const token = useLinkToken();
  const { user, refresh } = useAuth();
  const [state, setState] = useState<"working" | "done" | "failed">(token === "" ? "failed" : "working");
  const [message, setMessage] = useState("This link is invalid or has expired.");
  const started = useRef(false);

  useEffect(() => {
    if (token === "" || started.current) {
      return;
    }
    started.current = true;
    auth.verifyEmail(token).then(
      () => {
        setState("done");
        void refresh();
      },
      (failure: unknown) => {
        setMessage(describeError(failure));
        setState("failed");
      },
    );
  }, [token, refresh]);

  return (
    <AuthFrame title="Confirm your email">
      {state === "working" ? <Loading label="Confirming…" /> : null}
      {state === "done" ? <p role="status">Your email address is confirmed.</p> : null}
      {state === "failed" ? <ErrorText message={message} /> : null}
      {state === "working" ? null : (
        <p>
          <Link to={user === null ? "/login" : "/"}>{user === null ? "Sign in" : "Go to your decks"}</Link>
        </p>
      )}
    </AuthFrame>
  );
}

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const submit = useSubmit();

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    void submit.run(async () => {
      await auth.requestPasswordReset(email);
      setSent(true);
    });
  };

  return (
    <AuthFrame title="Reset your password">
      {sent ? (
        <p role="status">If that address has an account, a reset link is on its way. The link works once.</p>
      ) : (
        <form onSubmit={onSubmit} className="stack">
          <EmailField value={email} onChange={setEmail} />
          <ErrorText message={submit.error} />
          <button type="submit" className="button primary" disabled={submit.busy}>
            Send reset link
          </button>
        </form>
      )}
      <p className="muted">
        <Link to="/login">Back to sign in</Link>
      </p>
    </AuthFrame>
  );
}

export function ResetPasswordPage() {
  const token = useLinkToken();
  const [password, setPassword] = useState("");
  const [done, setDone] = useState(false);
  const submit = useSubmit();

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    void submit.run(async () => {
      await auth.confirmPasswordReset(token, password);
      setDone(true);
    });
  };

  let body: ReactNode;
  if (token === "") {
    body = <ErrorText message="This link is invalid or has expired." />;
  } else if (done) {
    body = <p role="status">Your password has been changed and other sessions were signed out.</p>;
  } else {
    body = (
      <form onSubmit={onSubmit} className="stack">
        <PasswordField label="New password" value={password} onChange={setPassword} autoComplete="new-password" />
        <ErrorText message={submit.error} />
        <button type="submit" className="button primary" disabled={submit.busy}>
          Change password
        </button>
      </form>
    );
  }

  return (
    <AuthFrame title="Choose a new password">
      {body}
      <p className="muted">
        <Link to="/login">Sign in</Link>
      </p>
    </AuthFrame>
  );
}
