// SPDX-License-Identifier: AGPL-3.0-only
// Where the servers listen and where the run keeps its files. The ports are
// uncommon ones so a developer's own dev servers do not collide with the suite.
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const API_PORT = Number(process.env.E2E_API_PORT ?? "18765");
export const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? "14173");
export const API_URL = `http://127.0.0.1:${String(API_PORT)}`;
// Links in emails use this origin, and the session cookie is set for this host,
// so the browser and the API helpers always use exactly this address.
export const WEB_URL = `http://127.0.0.1:${String(WEB_PORT)}`;

const here = dirname(fileURLToPath(import.meta.url));
export const RUN_DIR = resolve(here, "../.run");
export const API_LOG = resolve(RUN_DIR, "api.log");
export const ADMIN_STATE = resolve(RUN_DIR, "admin-state.json");
