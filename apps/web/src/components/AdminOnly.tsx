// SPDX-License-Identifier: AGPL-3.0-only
// Screens that only an admin uses. Members study and read decks; managing cards
// in bulk is the admin's job, so a member reaching such a screen by a typed or
// old link sees a short notice pointing back to their decks.

import type { ReactNode } from "react";
import { Link } from "react-router";

import { useAuth } from "../auth/AuthContext";
import { useDocumentTitle } from "../lib/useDocumentTitle";

function AdminNotice({ title }: { title: string }) {
  useDocumentTitle(title);
  return (
    <section>
      <h1>{title}</h1>
      <p>This page is for administrators. To read a deck's cards, open the deck and choose Read cards.</p>
      <p>
        <Link className="button primary" to="/">
          Back to your decks
        </Link>
      </p>
    </section>
  );
}

export function AdminOnly({ title, children }: { title: string; children: ReactNode }) {
  const { user } = useAuth();
  return user?.role === "admin" ? <>{children}</> : <AdminNotice title={title} />;
}
