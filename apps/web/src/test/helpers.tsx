// SPDX-License-Identifier: AGPL-3.0-only
// Test helpers: a scripted fetch, a fake EventSource and an app renderer.

import { render, type RenderResult } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";

import { App } from "../App";
import type { User } from "../api/types";

export interface Call {
  method: string;
  path: string;
  body: unknown;
}

type Reply = { status?: number; body?: unknown } | Error;
type Handler = Reply | ((call: Call) => Reply | Promise<Reply>);

/**
 * Replace fetch with a router keyed by "METHOD /path" (the /api prefix and the
 * query string removed). Unmatched requests answer 404. Returns the call log.
 */
export function mockApi(routes: Record<string, Handler>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(input instanceof Request ? input.url : input.toString(), "http://localhost");
      const method = init?.method ?? "GET";
      const path = url.pathname.replace(/^\/api/, "");
      let body: unknown = undefined;
      if (typeof init?.body === "string") {
        body = JSON.parse(init.body);
      } else if (init?.body instanceof FormData) {
        body = Object.fromEntries(init.body.entries());
      }
      const call: Call = { method, path: `${path}${url.search}`, body };
      calls.push(call);
      const handler = routes[`${method} ${path}${url.search}`] ?? routes[`${method} ${path}`];
      const reply: Reply =
        handler === undefined
          ? { status: 404, body: { detail: "Not found." } }
          : typeof handler === "function"
            ? await handler(call)
            : handler;
      if (reply instanceof Error) {
        throw reply;
      }
      const status = reply.status ?? 200;
      const text = reply.body === undefined ? "" : JSON.stringify(reply.body);
      return new Response(status === 204 ? null : text, { status });
    }),
  );
  return calls;
}

export const USER: User = { id: "u1", email: "ada@example.com", role: "member", email_verified: true };

export function renderApp(path: string): RenderResult {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

type Listener = (event: MessageEvent<unknown>) => void;

/** A controllable stand-in for EventSource. */
export class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];

  readonly url: string;
  readyState = FakeEventSource.OPEN;
  onerror: (() => void) | null = null;
  private listeners = new Map<string, Listener[]>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: Listener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.readyState = FakeEventSource.CLOSED;
  }

  emit(type: string, data: unknown): void {
    const event = new MessageEvent(type, { data });
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }

  fail(readyState: number): void {
    this.readyState = readyState;
    this.onerror?.();
  }
}
