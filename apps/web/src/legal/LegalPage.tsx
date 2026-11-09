// SPDX-License-Identifier: AGPL-3.0-only
// The operator's privacy policy, terms and imprint (F-LEGAL-1). Public, so a
// visitor can read them before signing up. The Markdown is the operator's, so it
// goes through the same sanitizer as card text. Loaded on first visit.

import { Link } from "react-router";

import { legal, type LegalPageName } from "../api/account";
import { useAuth } from "../auth/AuthContext";
import { SiteFooter } from "../components/SiteFooter";
import { ErrorText, Loading } from "../components/ui";
import { renderMarkdown } from "../lib/markdown";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { useResource } from "../lib/useResource";

const TITLES: Record<LegalPageName, string> = {
  privacy: "Privacy policy",
  terms: "Terms of use",
  imprint: "Imprint",
};

export function LegalPage({ page }: { page: LegalPageName }) {
  useDocumentTitle(TITLES[page]);
  const { user } = useAuth();
  const content = useResource(() => legal.page(page), `legal:${page}`);
  return (
    <>
      <main id="main" className="legal">
        <p>
          <Link to="/" className="brand">
            Flash-Cards
          </Link>
        </p>
        <h1>{TITLES[page]}</h1>
        {content.loading ? <Loading /> : null}
        <ErrorText message={content.error} />
        {content.data === undefined ? null : (
          <>
            {content.data.is_template ? (
              <p className="banner" role="note">
                This server&apos;s operator has not written this page yet, so what follows is the unfilled template.
              </p>
            ) : null}
            <div
              className="card-content legal-text"
              // renderMarkdown sanitizes its output with DOMPurify.
              dangerouslySetInnerHTML={{ __html: renderMarkdown(content.data.markdown, { math: false }) }}
            />
          </>
        )}
        <p>
          <Link to={user === null ? "/login" : "/"}>{user === null ? "Sign in" : "Back to your decks"}</Link>
        </p>
      </main>
      <SiteFooter />
    </>
  );
}
