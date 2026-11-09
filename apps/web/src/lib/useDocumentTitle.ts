// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect } from "react";

export const APP_NAME = "Flash-Cards";

/** The browser tab and history title for the page on screen: "Page · Flash-Cards". */
export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = `${title} · ${APP_NAME}`;
  }, [title]);
}
