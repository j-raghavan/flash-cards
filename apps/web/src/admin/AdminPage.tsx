// SPDX-License-Identifier: AGPL-3.0-only
// The admin console (F-ADMIN-1 to F-ADMIN-5): users and their AI quotas,
// invites, instance quotas, AI usage and the audit log. The server checks the admin role on every call; this screen only
// hides itself from members and keeps admins from acting on their own account.
// Loaded on first visit, so members never download it.

import { useRef, useState, type KeyboardEvent, type SubmitEvent } from "react";

import { adminUsage } from "../api/adminUsage";
import { describeError } from "../api/client";
import { admin } from "../api/endpoints";
import { twoFactor } from "../api/twoFactor";
import type { AdminUser, AuditEvent, CreatedInvite, Invite, Role } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { ConfirmButton, CopyField, ErrorText, Loading } from "../components/ui";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useResource } from "../lib/useResource";
import { useSubmit } from "../lib/useSubmit";
import { QuotasPanel } from "./QuotasPanel";
import { UsagePanel } from "./UsagePanel";
import { UserQuotaEditor } from "./UserQuota";

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function formatDay(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
}

/** The link an invited person signs up with; the token sits in the fragment. */
export function inviteUrl(token: string): string {
  return `${window.location.origin}/signup#token=${encodeURIComponent(token)}`;
}

// --- users ---

function UserRow({
  account,
  self,
  onChanged,
  onRemoved,
}: {
  account: AdminUser;
  self: boolean;
  onChanged: (user: AdminUser) => void;
  onRemoved: (userId: string) => void;
}) {
  const action = useSubmit();
  const [note, setNote] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [quotaOpen, setQuotaOpen] = useState(false);
  const quotaButton = useRef<HTMLButtonElement>(null);
  const closeQuota = (): void => {
    setQuotaOpen(false);
    quotaButton.current?.focus();
  };

  const act = (run: () => Promise<AdminUser>, done: string): void => {
    setNote(null);
    void action.run(async () => {
      onChanged(await run());
      setNote(done);
    });
  };

  return (
    <>
      <tr>
        <th scope="row">
          {account.email}
          {self ? <span className="muted"> (you)</span> : null}
        </th>
        <td>
          <select
            aria-label={`Role of ${account.email}`}
            value={account.role}
            disabled={self || action.busy}
            onChange={(event) => {
              const role = event.target.value as Role;
              act(() => admin.setRole(account.id, role), `Role set to ${role}.`);
            }}
          >
            <option value="member">Member</option>
            <option value="admin">Admin</option>
          </select>
        </td>
        <td>
          {account.suspended ? "Suspended" : "Active"}
          {account.email_verified ? "" : ", email not confirmed"}
          {account.two_factor === true ? ", two-step sign-in on" : ""}
        </td>
        <td>{formatDay(account.created_at)}</td>
        <td>
          <div className="row">
            <button
              type="button"
              className="button"
              ref={quotaButton}
              aria-expanded={quotaOpen}
              aria-controls={quotaOpen ? `quota-row-${account.id}` : undefined}
              aria-label={`AI quota for ${account.email}`}
              onClick={() => {
                if (quotaOpen) {
                  closeQuota();
                } else {
                  setQuotaOpen(true);
                }
              }}
            >
              AI quota
            </button>
            {self ? null : (
              <>
                {account.suspended ? (
                  <button
                    type="button"
                    className="button"
                    disabled={action.busy}
                    aria-label={`Unsuspend ${account.email}`}
                    onClick={() => {
                      act(() => admin.unsuspend(account.id), "Unsuspended.");
                    }}
                  >
                    Unsuspend
                  </button>
                ) : (
                  <button
                    type="button"
                    className="button"
                    disabled={action.busy}
                    aria-label={`Suspend ${account.email}`}
                    onClick={() => {
                      act(() => admin.suspend(account.id), "Suspended; their sessions have ended.");
                    }}
                  >
                    Suspend
                  </button>
                )}
                <button
                  type="button"
                  className="button"
                  disabled={action.busy}
                  aria-label={`Unlock sign-in for ${account.email}`}
                  onClick={() => {
                    act(() => admin.unlock(account.id), "Sign-in unlocked.");
                  }}
                >
                  Unlock
                </button>
                {account.two_factor === true ? (
                  <ConfirmButton
                    label={`Reset two-step sign-in for ${account.email}`}
                    confirmLabel="Reset two-step sign-in"
                    prompt={`Remove the authenticator app, passkeys and recovery codes of ${account.email}? They can then sign in with their password alone. Do this only after confirming who is asking.`}
                    onConfirm={async () => {
                      setNote(null);
                      setRemoveError(null);
                      try {
                        onChanged(await twoFactor.adminReset(account.id));
                        setNote("Two-step sign-in reset.");
                      } catch (failure) {
                        setRemoveError(describeError(failure));
                      }
                    }}
                  />
                ) : null}
                <ConfirmButton
                  label={`Delete ${account.email}`}
                  confirmLabel="Delete account"
                  prompt={`Delete ${account.email} and all their decks, cards and history? This cannot be undone.`}
                  onConfirm={async () => {
                    setRemoveError(null);
                    try {
                      await admin.removeUser(account.id);
                      onRemoved(account.id);
                    } catch (failure) {
                      setRemoveError(describeError(failure));
                    }
                  }}
                />
              </>
            )}
          </div>
          <span role="status" className="muted">
            {note}
          </span>
          <ErrorText message={action.error} />
          <ErrorText message={removeError} />
        </td>
      </tr>
      {quotaOpen ? (
        <tr id={`quota-row-${account.id}`}>
          <td colSpan={5}>
            <UserQuotaEditor account={account} onClose={closeQuota} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function UsersPanel({ selfId }: { selfId: string }) {
  const users = useResource(() => admin.users(), "admin:users");
  if (users.error !== null) {
    return <ErrorText message={users.error} />;
  }
  if (users.data === undefined) {
    return <Loading />;
  }
  const list = users.data;
  return (
    <div className="table-wrap">
      <table className="browser">
        <caption className="visually-hidden">Users</caption>
        <thead>
          <tr>
            <th scope="col">Email</th>
            <th scope="col">Role</th>
            <th scope="col">Status</th>
            <th scope="col">Joined</th>
            <th scope="col">
              <span className="visually-hidden">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {list.map((account) => (
            <UserRow
              key={account.id}
              account={account}
              self={account.id === selfId}
              onChanged={(changed) => {
                // Most answers do not look up two-step sign-in (null): keep what the list said.
                users.setData((current = []) =>
                  current.map((u) =>
                    u.id === changed.id ? { ...changed, two_factor: changed.two_factor ?? u.two_factor ?? null } : u,
                  ),
                );
              }}
              onRemoved={(userId) => {
                users.setData((current = []) => current.filter((u) => u.id !== userId));
              }}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- invites ---

function InvitesPanel() {
  const invites = useResource(() => admin.invites(), "admin:invites");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [days, setDays] = useState("7");
  const [created, setCreated] = useState<CreatedInvite | null>(null);
  const [revokeError, setRevokeError] = useState<string | null>(null);
  const [resent, setResent] = useState<CreatedInvite | null>(null);
  const [resendError, setResendError] = useState<string | null>(null);
  const submit = useSubmit();
  const dayCount = Number(days);
  const validDays = Number.isInteger(dayCount) && dayCount >= 1 && dayCount <= 90;

  const onSubmit = (event: SubmitEvent): void => {
    event.preventDefault();
    void submit.run(async () => {
      const invite = await admin.createInvite(email.trim() === "" ? null : email.trim(), role, dayCount);
      setCreated(invite);
      setResent(null);
      setEmail("");
      invites.reload();
    });
  };

  return (
    <>
      <form className="inline-form" onSubmit={onSubmit} aria-label="New invite">
        <label className="field grow">
          <span>Email (optional: limits the invite to this address)</span>
          <input
            type="email"
            maxLength={320}
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
            }}
          />
        </label>
        <label className="field">
          <span>Role</span>
          <select
            value={role}
            onChange={(event) => {
              setRole(event.target.value as Role);
            }}
          >
            <option value="member">Member</option>
            <option value="admin">Admin</option>
          </select>
        </label>
        <label className="field">
          <span>Valid for (days)</span>
          <input
            type="number"
            min={1}
            max={90}
            value={days}
            onChange={(event) => {
              setDays(event.target.value);
            }}
          />
        </label>
        <button type="submit" className="button primary" disabled={!validDays || submit.busy}>
          Create invite
        </button>
        <ErrorText message={submit.error} />
      </form>
      {created === null ? null : (
        <div className="panel" role="status">
          <p>
            <strong>Send this sign-up link now.</strong> It is shown only once and works until{" "}
            {formatTime(created.expires_at)}.
          </p>
          <CopyField label="Invite link" value={inviteUrl(created.token)} />
        </div>
      )}
      {resent === null ? null : (
        <div className="panel" role="status">
          <p>
            <strong>Invite emailed again to {resent.email}.</strong> The previous link no longer works. The new link is
            shown only once and works until {formatTime(resent.expires_at)}.
          </p>
          <CopyField label="New invite link" value={inviteUrl(resent.token)} />
        </div>
      )}
      <ErrorText message={invites.error} />
      <ErrorText message={revokeError} />
      <ErrorText message={resendError} />
      {invites.data === undefined ? (
        invites.error === null ? (
          <Loading />
        ) : null
      ) : invites.data.length === 0 ? (
        <p className="empty">No invites yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="browser">
            <caption className="visually-hidden">Invites</caption>
            <thead>
              <tr>
                <th scope="col">For</th>
                <th scope="col">Role</th>
                <th scope="col">Status</th>
                <th scope="col">Expires</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {invites.data.map((invite: Invite) => (
                <tr key={invite.id}>
                  <th scope="row">{invite.email ?? "Anyone with the link"}</th>
                  <td>{invite.role === "admin" ? "Admin" : "Member"}</td>
                  <td>{invite.status}</td>
                  <td>{formatDay(invite.expires_at)}</td>
                  <td>
                    {invite.status === "active" ? (
                      <div className="row">
                        {invite.email === null ? null : (
                          <ConfirmButton
                            label="Resend"
                            confirmLabel="Resend invite"
                            prompt={`Email a new sign-up link to ${invite.email}? The current link stops working; the expiry stays the same.`}
                            onConfirm={async () => {
                              setResendError(null);
                              try {
                                const rotated = await adminUsage.resendInvite(invite.id);
                                setCreated(null);
                                setResent(rotated);
                                invites.reload();
                              } catch (failure) {
                                setResendError(describeError(failure));
                              }
                            }}
                          />
                        )}
                        <ConfirmButton
                          label="Revoke"
                          confirmLabel="Revoke invite"
                          prompt="Revoke this invite? Its link stops working at once."
                          onConfirm={async () => {
                            setRevokeError(null);
                            try {
                              await admin.revokeInvite(invite.id);
                              // A link shown for this invite no longer works.
                              setCreated((shown) => (shown?.id === invite.id ? null : shown));
                              setResent((shown) => (shown?.id === invite.id ? null : shown));
                              invites.reload();
                            } catch (failure) {
                              setRevokeError(describeError(failure));
                            }
                          }}
                        />
                      </div>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

// --- audit log ---

export const AUDIT_PAGE = 50;

function detailText(detail: Record<string, string>): string {
  return Object.entries(detail)
    .map(([key, value]) => `${key}: ${value}`)
    .join(", ");
}

function AuditPanel() {
  // The cursor (last event id of the page before) of each older page visited.
  const [cursors, setCursors] = useState<string[]>([]);
  const before = cursors.length === 0 ? null : (cursors[cursors.length - 1] as string);
  const page = useResource(() => admin.audit(AUDIT_PAGE, before), `audit:${before ?? ""}`);
  const users = useResource(() => admin.users(), "admin:users");
  const emails = new Map((users.data ?? []).map((u) => [u.id, u.email]));
  const who = (id: string | null): string => (id === null ? "System" : (emails.get(id) ?? id));
  const events: AuditEvent[] = page.data ?? [];
  const last = events.length === AUDIT_PAGE ? events[events.length - 1] : undefined;

  return (
    <>
      <ErrorText message={page.error} />
      {page.data === undefined ? (
        page.error === null ? (
          <Loading />
        ) : null
      ) : events.length === 0 ? (
        <p className="empty">No events{cursors.length > 0 ? " before these" : " yet"}.</p>
      ) : (
        <div className="table-wrap">
          <table className="browser">
            <caption className="visually-hidden">Audit log, newest first</caption>
            <thead>
              <tr>
                <th scope="col">When</th>
                <th scope="col">Action</th>
                <th scope="col">By</th>
                <th scope="col">Target</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.id}>
                  <td>{formatTime(event.created_at)}</td>
                  <th scope="row">{event.action}</th>
                  <td>{who(event.actor_id)}</td>
                  <td>{event.target_id === null ? "" : who(event.target_id)}</td>
                  <td>{detailText(event.detail)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="row pager">
        {cursors.length === 0 ? (
          <span />
        ) : (
          <button
            type="button"
            className="button"
            onClick={() => {
              setCursors(cursors.slice(0, -1));
            }}
          >
            Newer
          </button>
        )}
        {last === undefined ? null : (
          <button
            type="button"
            className="button"
            onClick={() => {
              setCursors([...cursors, last.id]);
            }}
          >
            Older
          </button>
        )}
      </div>
    </>
  );
}

// --- the console ---

const TABS = [
  { id: "users", label: "Users" },
  { id: "invites", label: "Invites" },
  { id: "quotas", label: "Quotas" },
  { id: "usage", label: "Usage" },
  { id: "audit", label: "Audit log" },
] as const;
type TabId = (typeof TABS)[number]["id"];

export function AdminPage() {
  useDocumentTitle("Admin");
  const { user } = useAuth();
  const [tab, setTab] = useState<TabId>("users");
  const tabRefs = useRef(new Map<TabId, HTMLButtonElement>());

  if (user?.role !== "admin") {
    return (
      <section>
        <h1>Admin</h1>
        <p>This page is for administrators.</p>
      </section>
    );
  }

  // Arrow keys, Home and End move between tabs (the WAI-ARIA tabs pattern).
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    const index = TABS.findIndex((t) => t.id === tab);
    const next =
      event.key === "ArrowRight"
        ? (index + 1) % TABS.length
        : event.key === "ArrowLeft"
          ? (index - 1 + TABS.length) % TABS.length
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? TABS.length - 1
              : null;
    if (next === null) {
      return;
    }
    event.preventDefault();
    const id = (TABS[next] as (typeof TABS)[number]).id;
    setTab(id);
    tabRefs.current.get(id)?.focus();
  };

  return (
    <section>
      <h1>Admin</h1>
      <div className="tabs" role="tablist" aria-label="Admin sections" onKeyDown={onKeyDown}>
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={tab === t.id ? `panel-${t.id}` : undefined}
            tabIndex={tab === t.id ? 0 : -1}
            ref={(node) => {
              if (node !== null) {
                tabRefs.current.set(t.id, node);
              }
            }}
            onClick={() => {
              setTab(t.id);
            }}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="tab-panel" tabIndex={0}>
        {tab === "users" ? <UsersPanel selfId={user.id} /> : null}
        {tab === "invites" ? <InvitesPanel /> : null}
        {tab === "quotas" ? <QuotasPanel /> : null}
        {tab === "usage" ? <UsagePanel /> : null}
        {tab === "audit" ? <AuditPanel /> : null}
      </div>
    </section>
  );
}
