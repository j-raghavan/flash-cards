// SPDX-License-Identifier: AGPL-3.0-only
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useOfflineSync } from "../offline/useOfflineSync";
import { useResource } from "./useResource";

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  let reject: (reason: Error) => void = () => undefined;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("useResource", () => {
  it("ignores answers for a key it has moved on from", async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const third = deferred<string>();
    const loads = [first, second, third];
    const { result, rerender } = renderHook(({ k }) => useResource(() => (loads.shift() ?? third).promise, k), {
      initialProps: { k: "a" },
    });
    expect(result.current.loading).toBe(true);
    rerender({ k: "b" });
    first.resolve("stale");
    rerender({ k: "c" });
    second.reject(new Error("stale failure"));
    third.resolve("fresh");
    await waitFor(() => {
      expect(result.current.data).toBe("fresh");
    });
    expect(result.current.error).toBeNull();
  });

  it("keeps data across a failed reload and drops it for a new key", async () => {
    let fail = false;
    const { result, rerender } = renderHook(
      ({ k }) => useResource(() => (fail ? Promise.reject(new Error("nope")) : Promise.resolve(`data-${k}`)), k),
      { initialProps: { k: "a" } },
    );
    await waitFor(() => {
      expect(result.current.data).toBe("data-a");
    });
    fail = true;
    act(() => {
      result.current.reload();
    });
    await waitFor(() => {
      expect(result.current.error).toBe("nope");
    });
    expect(result.current.data).toBe("data-a");
    rerender({ k: "b" });
    expect(result.current.data).toBeUndefined();
    await waitFor(() => {
      expect(result.current.error).toBe("nope");
    });
    expect(result.current.data).toBeUndefined();
  });
});

describe("useOfflineSync", () => {
  it("does nothing without a user", async () => {
    const { result } = renderHook(() => useOfflineSync(null));
    await act(() => result.current.syncNow());
    expect(result.current.waiting).toBe(0);
    expect(result.current.online).toBe(true);
  });
});
