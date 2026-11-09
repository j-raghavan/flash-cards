// SPDX-License-Identifier: AGPL-3.0-only
// A small fetch wrapper: JSON in and out, the session cookie sent on every call,
// and the API's error bodies turned into readable messages.

/** Where the API lives. Same origin under /api unless VITE_API_BASE_URL says otherwise. */
export function apiBaseUrl(): string {
  const configured: unknown = import.meta.env.VITE_API_BASE_URL;
  const base = typeof configured === "string" && configured !== "" ? configured : "/api";
  return base.replace(/\/+$/, "");
}

export function apiUrl(path: string): string {
  return `${apiBaseUrl()}${path}`;
}

/** The server answered with an error status. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** The request never reached the server (offline, DNS, connection reset). */
export class NetworkError extends Error {
  constructor() {
    super("Could not reach the server. Check your connection and try again.");
    this.name = "NetworkError";
  }
}

interface ValidationIssue {
  msg?: unknown;
}

/** FastAPI sends {"detail": "..."} or, for validation errors, {"detail": [{msg}, ...]}. */
export function errorMessage(status: number, body: unknown): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail: unknown = body.detail;
    if (typeof detail === "string" && detail !== "") {
      return detail;
    }
    if (Array.isArray(detail)) {
      const messages = (detail as ValidationIssue[])
        .map((issue) => (typeof issue.msg === "string" ? issue.msg.replace(/^Value error, /, "") : ""))
        .filter((msg) => msg !== "");
      if (messages.length > 0) {
        return messages.join(" ");
      }
    }
  }
  return status >= 500 ? "Something went wrong on the server. Try again." : `Request failed (${status}).`;
}

let unauthorizedHandler: (() => void) | null = null;

/** Called whenever the API answers 401, so the app can return to sign-in. */
export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  json?: unknown;
  form?: FormData;
  signal?: AbortSignal;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (options.form !== undefined) {
    body = options.form;
  } else if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  }
  const init: RequestInit = {
    method: options.method ?? "GET",
    headers,
    credentials: "include",
  };
  if (body !== undefined) {
    init.body = body;
  }
  if (options.signal !== undefined) {
    init.signal = options.signal;
  }

  let response: Response;
  try {
    response = await fetch(apiUrl(path), init);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new NetworkError();
  }

  // 204, and a 202 with no body, carry nothing to parse.
  const text = response.status === 204 ? "" : await response.text();
  let parsed: unknown = undefined;
  if (text !== "") {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }
  if (!response.ok) {
    if (response.status === 401) {
      unauthorizedHandler?.();
    }
    throw new ApiError(response.status, errorMessage(response.status, parsed));
  }
  return parsed as T;
}

/** A message fit to show the user for any thrown value. */
export function describeError(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Something went wrong. Try again.";
}
