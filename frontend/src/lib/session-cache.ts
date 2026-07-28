type SessionCacheEnvelope<T> = {
  version: 1;
  storedAt: number;
  expiresAt: number;
  value: T;
};

export type SessionCacheOptions = {
  prefix: string;
  ttlMs: number;
  maxEntries: number;
  maxTotalBytes: number;
  maxEntryBytes: number;
};

type StoredSessionEntry = {
  key: string;
  storedAt: number;
  bytes: number;
};

function parseEnvelope<T>(raw: string): SessionCacheEnvelope<T> | null {
  try {
    const parsed = JSON.parse(raw) as Partial<SessionCacheEnvelope<T>> | null;
    if (
      !parsed ||
      parsed.version !== 1 ||
      typeof parsed.storedAt !== "number" ||
      typeof parsed.expiresAt !== "number" ||
      !("value" in parsed)
    ) {
      return null;
    }
    return parsed as SessionCacheEnvelope<T>;
  } catch {
    return null;
  }
}

export function cleanupSessionCache(
  options: Pick<SessionCacheOptions, "prefix" | "maxEntries" | "maxTotalBytes">,
  now = Date.now(),
): void {
  const entries: StoredSessionEntry[] = [];
  try {
    for (let index = window.sessionStorage.length - 1; index >= 0; index -= 1) {
      const key = window.sessionStorage.key(index);
      if (!key?.startsWith(options.prefix)) {
        continue;
      }
      const raw = window.sessionStorage.getItem(key);
      const envelope = raw ? parseEnvelope(raw) : null;
      if (!raw || !envelope || envelope.expiresAt <= now) {
        window.sessionStorage.removeItem(key);
        continue;
      }
      entries.push({ key, storedAt: envelope.storedAt, bytes: raw.length * 2 });
    }

    entries.sort((left, right) => right.storedAt - left.storedAt);
    let retainedBytes = 0;
    for (const [index, entry] of entries.entries()) {
      retainedBytes += entry.bytes;
      if (index >= options.maxEntries || retainedBytes > options.maxTotalBytes) {
        window.sessionStorage.removeItem(entry.key);
      }
    }
  } catch {
    // Storage can be disabled; the network path remains available.
  }
}

export function readSessionCache<T>(key: string, options: SessionCacheOptions, now = Date.now()): T | null {
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) {
      return null;
    }
    const envelope = parseEnvelope<T>(raw);
    if (!envelope || envelope.expiresAt <= now) {
      window.sessionStorage.removeItem(key);
      return null;
    }
    return envelope.value;
  } catch {
    return null;
  }
}

export function writeSessionCache(
  key: string,
  value: unknown,
  options: SessionCacheOptions,
  now = Date.now(),
): boolean {
  const envelope: SessionCacheEnvelope<unknown> = {
    version: 1,
    storedAt: now,
    expiresAt: now + options.ttlMs,
    value,
  };

  try {
    const serialized = JSON.stringify(envelope);
    if (serialized.length * 2 > options.maxEntryBytes) {
      window.sessionStorage.removeItem(key);
      cleanupSessionCache(options, now);
      return false;
    }
    window.sessionStorage.setItem(key, serialized);
    cleanupSessionCache(options, now);
    return window.sessionStorage.getItem(key) !== null;
  } catch {
    cleanupSessionCache(options, now);
    return false;
  }
}
