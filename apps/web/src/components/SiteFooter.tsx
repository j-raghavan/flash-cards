// SPDX-License-Identifier: AGPL-3.0-only
// Links to the operator's legal pages (F-LEGAL-1), on every screen.

import { Link } from "react-router";

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <nav aria-label="Legal">
        <Link to="/privacy">Privacy</Link>
        <Link to="/terms">Terms</Link>
        <Link to="/imprint">Imprint</Link>
      </nav>
    </footer>
  );
}
