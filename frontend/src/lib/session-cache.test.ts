import { beforeEach, describe, expect, it } from "vitest";

import {
  cleanupSessionCache,
  readSessionCache,
  writeSessionCache,
  type SessionCacheOptions,
} from "./session-cache";

const options: SessionCacheOptions = {
  prefix: "test-cache:",
  ttlMs: 1_000,
  maxEntries: 2,
  maxTotalBytes: 10_000,
  maxEntryBytes: 1_000,
};

describe("session cache", () => {
  beforeEach(() => window.sessionStorage.clear());

  it("expires entries and deletes legacy cache values", () => {
    expect(writeSessionCache("test-cache:fresh", { value: 1 }, options, 1_000)).toBe(true);
    expect(readSessionCache("test-cache:fresh", options, 1_500)).toEqual({ value: 1 });
    expect(readSessionCache("test-cache:fresh", options, 2_001)).toBeNull();
    expect(window.sessionStorage.getItem("test-cache:fresh")).toBeNull();

    window.sessionStorage.setItem("test-cache:legacy", JSON.stringify({ value: 2 }));
    expect(readSessionCache("test-cache:legacy", options, 1_500)).toBeNull();
    expect(window.sessionStorage.getItem("test-cache:legacy")).toBeNull();
  });

  it("keeps only the newest entries within the configured budget", () => {
    writeSessionCache("test-cache:a", "a", options, 1_000);
    writeSessionCache("test-cache:b", "b", options, 1_100);
    writeSessionCache("test-cache:c", "c", options, 1_200);

    expect(window.sessionStorage.getItem("test-cache:a")).toBeNull();
    expect(readSessionCache("test-cache:b", options, 1_300)).toBe("b");
    expect(readSessionCache("test-cache:c", options, 1_300)).toBe("c");
  });

  it("refuses oversized entries and removes expired values during cleanup", () => {
    expect(writeSessionCache("test-cache:large", "x".repeat(1_000), options, 1_000)).toBe(false);
    expect(window.sessionStorage.getItem("test-cache:large")).toBeNull();

    writeSessionCache("test-cache:old", "old", options, 1_000);
    cleanupSessionCache(options, 2_001);
    expect(window.sessionStorage.getItem("test-cache:old")).toBeNull();
  });
});
