// SPDX-License-Identifier: AGPL-3.0-only
// A short AI data-use notice before a first upload (F-LEGAL-2). Dismissed once
// per user on this device; the full disclosure stays on the account page.

import { useId, useState } from "react";
import { Link } from "react-router";

import { aiNoticeSeen, legal, rememberAiNoticeSeen } from "../api/account";
import { useAuth } from "../auth/AuthContext";
import { useResource } from "../lib/useResource";

function NoticeText() {
  const disclosure = useResource(legal.aiDisclosure, "ai-disclosure").data;
  if (disclosure === undefined) {
    return <p>Files you upload here are sent to this server&apos;s AI provider to draft cards.</p>;
  }
  if (disclosure.provider === null) {
    return <p>{disclosure.statements.join(" ")}</p>;
  }
  return (
    <p>
      Files you upload here are sent to {disclosure.provider.name} to draft cards.
      {disclosure.trains_on_user_content
        ? " Your content may be used to train AI models; read the full notice before uploading."
        : " Your content is never used to train AI models."}
    </p>
  );
}

export function AiNotice() {
  const { user } = useAuth();
  const userId = user?.id ?? "";
  const [seen, setSeen] = useState(() => aiNoticeSeen(userId));
  const headingId = useId();
  if (seen) {
    return null;
  }
  return (
    <aside className="panel" aria-labelledby={headingId}>
      <h2 id={headingId}>How your files are used</h2>
      <NoticeText />
      <div className="row">
        <Link to="/account#ai-data">Read the full AI data-use notice</Link>
        <button
          type="button"
          className="button"
          onClick={() => {
            rememberAiNoticeSeen(userId);
            setSeen(true);
          }}
        >
          Got it
        </button>
      </div>
    </aside>
  );
}
