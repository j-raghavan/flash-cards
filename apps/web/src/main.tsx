// SPDX-License-Identifier: AGPL-3.0-only
import "katex/dist/katex.min.css";
import "./styles.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { registerSW } from "virtual:pwa-register";

import { App } from "./App";
import { restoreAppearance } from "./lib/appearance";

registerSW({ immediate: true });
// Paint in the last look used here before anything renders.
restoreAppearance();

const root = document.getElementById("root");
if (root !== null) {
  createRoot(root).render(
    <StrictMode>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </StrictMode>,
  );
}
