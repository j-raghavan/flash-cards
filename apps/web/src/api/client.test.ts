// SPDX-License-Identifier: AGPL-3.0-only
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../test/helpers";
import {
  ApiError,
  NetworkError,
  apiBaseUrl,
  apiUrl,
  describeError,
  errorMessage,
  onUnauthorized,
  request,
} from "./client";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("apiBaseUrl", () => {
  it("defaults to /api on the same origin", () => {
    vi.stubEnv("VITE_API_BASE_URL", "");
    expect(apiBaseUrl()).toBe("/api");
    expect(apiUrl("/decks")).toBe("/api/decks");
  });

  it("uses the configured base without a trailing slash", () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.com/");
    expect(apiUrl("/decks")).toBe("https://api.example.com/decks");
  });
});

describe("errorMessage", () => {
  it("uses a string detail", () => {
    expect(errorMessage(409, { detail: "Taken." })).toBe("Taken.");
  });

  it("joins validation messages and drops the pydantic prefix", () => {
    const body = { detail: [{ msg: "Value error, move needs a deck_id" }, { msg: "too long" }, { loc: [] }] };
    expect(errorMessage(422, body)).toBe("move needs a deck_id too long");
  });

  it("falls back when there is nothing readable", () => {
    expect(errorMessage(422, { detail: [{ loc: [] }] })).toBe("Request failed (422).");
    expect(errorMessage(400, { detail: "" })).toBe("Request failed (400).");
    expect(errorMessage(404, null)).toBe("Request failed (404).");
    expect(errorMessage(400, "text")).toBe("Request failed (400).");
    expect(errorMessage(503, {})).toBe("Something went wrong on the server. Try again.");
  });
});

describe("request", () => {
  it("sends JSON with the cookie and parses the answer", async () => {
    const calls = mockApi({ "POST /decks": { status: 201, body: { id: "d1" } } });
    await expect(request("/decks", { method: "POST", json: { name: "A" } })).resolves.toEqual({ id: "d1" });
    expect(calls[0]).toEqual({ method: "POST", path: "/decks", body: { name: "A" } });
    const init = vi.mocked(fetch).mock.calls[0]?.[1];
    expect(init?.credentials).toBe("include");
    expect(init?.headers).toMatchObject({ "Content-Type": "application/json" });
  });

  it("sends a form without a JSON content type", async () => {
    mockApi({ "POST /ai/generate": { status: 202, body: { id: "j" } } });
    const form = new FormData();
    form.append("deck_id", "d1");
    await request("/ai/generate", { method: "POST", form });
    const init = vi.mocked(fetch).mock.calls[0]?.[1];
    expect(init?.headers).not.toHaveProperty("Content-Type");
    expect(init?.body).toBe(form);
  });

  it("returns nothing for 204 and an empty 202, and the body of a 202 that has one", async () => {
    mockApi({
      "POST /auth/logout": { status: 204 },
      "POST /auth/password-reset": { status: 202 },
      "POST /ai/generate": { status: 202, body: { id: "j1" } },
    });
    await expect(request("/auth/logout", { method: "POST" })).resolves.toBeUndefined();
    await expect(request("/auth/password-reset", { method: "POST" })).resolves.toBeUndefined();
    await expect(request("/ai/generate", { method: "POST" })).resolves.toEqual({ id: "j1" });
  });

  it("returns null for a non-JSON success body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response("not json", { status: 200 }))),
    );
    await expect(request("/x")).resolves.toBeNull();
  });

  it("passes an abort signal through", async () => {
    mockApi({ "GET /x": { body: 1 } });
    const controller = new AbortController();
    await request("/x", { signal: controller.signal });
    expect(vi.mocked(fetch).mock.calls[0]?.[1]?.signal).toBe(controller.signal);
  });

  it("raises ApiError with the server's message", async () => {
    mockApi({ "GET /decks/x": { status: 404, body: { detail: "Not found." } } });
    const error = await request("/decks/x").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, message: "Not found." });
  });

  it("tells the app when the session is gone", async () => {
    mockApi({ "GET /auth/me": { status: 401, body: { detail: "Not authenticated." } } });
    const handler = vi.fn();
    onUnauthorized(handler);
    await expect(request("/auth/me")).rejects.toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalledOnce();
    onUnauthorized(null);
    await expect(request("/auth/me")).rejects.toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalledOnce();
  });

  it("turns a failed connection into a NetworkError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );
    await expect(request("/decks")).rejects.toBeInstanceOf(NetworkError);
  });

  it("lets an abort through unchanged", async () => {
    const abort = new DOMException("aborted", "AbortError");
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(abort)),
    );
    await expect(request("/decks")).rejects.toBe(abort);
  });
});

describe("describeError", () => {
  it("uses an error's message and a fallback otherwise", () => {
    expect(describeError(new NetworkError())).toMatch(/Could not reach the server/);
    expect(describeError("boom")).toBe("Something went wrong. Try again.");
  });
});
