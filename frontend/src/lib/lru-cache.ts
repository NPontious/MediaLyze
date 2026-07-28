type LruCacheEntry<V> = {
  value: V;
  expiresAt: number | null;
};

type LruCacheOptions = {
  ttlMs?: number;
};

export class LruCache<K, V> {
  private values = new Map<K, LruCacheEntry<V>>();

  constructor(
    private readonly limit: number,
    private readonly options: LruCacheOptions = {},
  ) {}

  get(key: K): V | undefined {
    const entry = this.values.get(key);
    if (entry === undefined) {
      return undefined;
    }
    if (entry.expiresAt !== null && entry.expiresAt <= Date.now()) {
      this.values.delete(key);
      return undefined;
    }
    this.values.delete(key);
    this.values.set(key, entry);
    return entry.value;
  }

  set(key: K, value: V): void {
    this.pruneExpired();
    this.values.delete(key);
    this.values.set(key, {
      value,
      expiresAt: this.options.ttlMs === undefined ? null : Date.now() + this.options.ttlMs,
    });

    while (this.values.size > this.limit) {
      const oldestKey = this.values.keys().next().value as K | undefined;
      if (oldestKey === undefined) {
        return;
      }
      this.values.delete(oldestKey);
    }
  }

  delete(key: K): boolean {
    return this.values.delete(key);
  }

  clear(): void {
    this.values.clear();
  }

  private pruneExpired(): void {
    const now = Date.now();
    for (const [key, entry] of this.values) {
      if (entry.expiresAt !== null && entry.expiresAt <= now) {
        this.values.delete(key);
      }
    }
  }
}
