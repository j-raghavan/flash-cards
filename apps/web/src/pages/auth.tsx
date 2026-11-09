// SPDX-License-Identifier: AGPL-3.0-only
// Sign-in, invite and public sign-up, email verification and password reset
// (F-AUTH-1, F-AUTH-2, F-AUTH-7).

import { useEffect, useId, useRef, useState, type SubmitEvent, type ReactNode } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router";

import { signup } from "../api/account";
import { ApiError, describeError } from "../api/client";
import { auth } from "../api/endpoints";
import { needsSecondFactor, type SecondFactorRequired } from "../api/twoFactor";
import type { User } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { SiteFooter } from "../components/SiteFooter";
import { SecondFactorStep } from "../auth/SecondFactorStep";
import { ErrorText, Loading } from "../components/ui";
import { safeNextPath, tokenFromLocation } from "../lib/text";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useResource } from "../lib/useResource";
import { useSubmit } from "../lib/useSubmit";

const MIN_PASSWORD_LENGTH = 12;

function AuthFrame({ title, children }: { title: string; children: ReactNode }) {
  useDocumentTitle(title);
  return (
    <>
      <main id="main" className="auth">
        <p className="brand">Flash-Cards</p>
        <h1>{title}</h1>
        {children}
      </main>
      <SiteFooter />
    </>
  );
}

/** Shown before an account is created: what signing up agrees to (F-LEGAL-1). */
function LegalConsent() {
  return (
    <p className="muted">
      By creating an account you agree to the <Link to="/terms">terms of use</Link> and confirm you have read the{" "}
      <Link to="/privacy">privacy policy</Link>.
    </p>
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
  const [pending, setPending] = useState<SecondFactorRequired | null>(null);
  const [restartNote, setRestartNote] = useState<string | null>(null);
  const submit = useSubmit();
  const location = useLocation();
  const next = safeNextPath(params.get("next") ?? new URLSearchParams(location.hash.replace(/^#/, "")).get("next"));
  const [accountDeleted] = useState(
    () => (location.state as { accountDeleted?: unknown } | null)?.accountDeleted === true,
  );
  useEffect(() => {
    // Shown once: a reload or Back does not repeat it.
    if (accountDeleted) {
      void navigate(`${location.pathname}${location.search}`, { replace: true, state: null });
    }
    // Only on arrival.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (user !== null) {
    return <Navigate to={next} replace />;
  }

  const finished = (signedInUser: User): void => {
    signedIn(signedInUser);
    void navigate(next, { replace: true });
  };

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    setRestartNote(null);
    void submit.run(async () => {
      const result = await auth.login(email, password);
      if (needsSecondFactor(result)) {
        setPassword("");
        setPending(result);
        return;
      }
      finished(result);
    });
  };

  if (pending !== null) {
    return (
      <AuthFrame title="Confirm it's you">
        <SecondFactorStep
          pending={pending}
          onSignedIn={finished}
          onRestart={(message) => {
            setPending(null);
            setRestartNote(message);
          }}
        />
      </AuthFrame>
    );
  }

  return (
    <AuthFrame title="Sign in">
      {accountDeleted ? <p role="status">Your account has been deleted.</p> : null}
      {restartNote === null ? null : <p role="status">{restartNote}</p>}
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
      <SignupHint />
    </AuthFrame>
  );
}

const INVITE_ONLY = "Flash-Cards is invite-only. Use the link in your invitation to create an account.";

/** Whether this server takes public sign-ups; when unknown, it is treated as invite-only. */
function useSignupOpen(): boolean | undefined {
  const options = useResource(signup.options, "auth-options");
  if (options.loading) {
    return undefined;
  }
  return options.data?.invite_only === false;
}

function SignupHint() {
  const open = useSignupOpen();
  if (open === undefined) {
    return null;
  }
  return open ? (
    <p className="muted">
      New here? <Link to="/signup">Create an account</Link>
    </p>
  ) : (
    <p className="muted">{INVITE_ONLY}</p>
  );
}

/** Public sign-up: the answer is the same for a new or a known address, and it never signs in. */
function PublicSignup() {
  const open = useSignupOpen();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [sent, setSent] = useState(false);
  const submit = useSubmit();

  if (open === undefined) {
    return (
      <AuthFrame title="Create your account">
        <Loading />
      </AuthFrame>
    );
  }
  if (!open) {
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
      try {
        await signup.register(email, password);
      } catch (failure) {
        if (failure instanceof ApiError && failure.status === 404) {
          throw new Error(INVITE_ONLY, { cause: failure });
        }
        throw failure;
      }
      setPassword("");
      setSent(true);
    });
  };

  return (
    <AuthFrame title={sent ? "Check your email" : "Create your account"}>
      {sent ? (
        <p role="status">
          If the address can be registered, a confirmation link is on its way. Follow it, then sign in.
        </p>
      ) : (
        <form onSubmit={onSubmit} className="stack">
          <EmailField value={email} onChange={setEmail} />
          <PasswordField label="Password" value={password} onChange={setPassword} autoComplete="new-password" />
          <LegalConsent />
          <ErrorText message={submit.error} />
          <button type="submit" className="button primary" disabled={submit.busy}>
            {submit.busy ? "Creating…" : "Create account"}
          </button>
        </form>
      )}
      <p className="muted">
        <Link to="/login">Already have an account? Sign in</Link>
      </p>
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
    return <PublicSignup />;
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
        <LegalConsent />
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
