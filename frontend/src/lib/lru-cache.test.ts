import { afterEach, describe, expect, it, vi } from "vitest";

import { LruCache } from "./lru-cache";

describe("LruCache", () => {
  afterEach(() => vi.useRealTimers());

  it("evicts the least recently used value when the limit is exceeded", () => {
    const cache = new LruCache<string, number>(2);

    cache.set("a", 1);
    cache.set("b", 2);
    expect(cache.get("a")).toBe(1);

    cache.set("c", 3);

    expect(cache.get("b")).toBeUndefined();
    expect(cache.get("a")).toBe(1);
    expect(cache.get("c")).toBe(3);
  });

  it("drops expired values instead of retaining stale data", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-28T10:00:00Z"));
    const cache = new LruCache<string, number>(2, { ttlMs: 1_000 });

    cache.set("a", 1);
    vi.advanceTimersByTime(1_001);

    expect(cache.get("a")).toBeUndefined();
  });
});
