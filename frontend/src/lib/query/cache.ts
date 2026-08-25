import { describeError, isAbortError, type DescribedError } from '@/api/errors';
import { hashKey, matchesPrefix, parseKey, type QueryKey } from './key';

export interface QueryState<T> {
  status: 'idle' | 'pending' | 'success' | 'error';
  data: T | undefined;
  error: DescribedError | null;
  refetchError: DescribedError | null;
  isFetching: boolean;
  dataUpdatedAt: number;
}

export interface QueryConfig<T> {
  fetcher: (context: { signal: AbortSignal }) => Promise<T>;
  fallbackMessage: string;
  staleTime: number;
  gcTime: number;
  refetchOnFocus: boolean;
  onRefetchError: 'keep' | 'discard';
}

export interface OptimisticPatch {
  key: QueryKey;
  update: (previous: unknown) => unknown;
}

interface Entry<T = unknown> {
  key: QueryKey;
  config: QueryConfig<T>;
  state: QueryState<T>;
  subscribers: Set<() => void>;
  promise: Promise<void> | null;
  controller: AbortController | null;
  trailing: boolean;
  invalidated: boolean;
  optimisticDepth: number;
  collectAt: number | null;
}

export const DEFAULT_GC_TIME = 5 * 60_000;

export const IDLE_STATE: QueryState<never> = Object.freeze({
  status: 'idle' as const,
  data: undefined,
  error: null,
  refetchError: null,
  isFetching: false,
  dataUpdatedAt: 0,
});

function mergeConfig<T>(previous: QueryConfig<T>, next: QueryConfig<T>): QueryConfig<T> {
  return {
    fetcher: previous.fetcher,
    fallbackMessage: previous.fallbackMessage,
    onRefetchError: previous.onRefetchError,
    staleTime: Math.min(previous.staleTime, next.staleTime),
    gcTime: Math.max(previous.gcTime, next.gcTime),
    refetchOnFocus: previous.refetchOnFocus || next.refetchOnFocus,
  };
}

export class QueryCache {
  private entries = new Map<string, Entry>();

  private focusHandler: (() => void) | null = null;

  getState<T>(key: QueryKey): QueryState<T> {
    const entry = this.entries.get(hashKey(key));
    return (entry?.state as QueryState<T> | undefined) ?? (IDLE_STATE as QueryState<T>);
  }

  subscribe<T>(key: QueryKey, config: QueryConfig<T>, listener: () => void): () => void {
    const entry = this.ensure(key, config);
    entry.config = mergeConfig(entry.config, config);
    entry.subscribers.add(listener);
    entry.collectAt = null;
    this.sweep();
    this.bindFocus();
    void this.maybeFetch(entry);

    return () => {
      entry.subscribers.delete(listener);
      if (entry.subscribers.size === 0) {
        entry.collectAt = Date.now() + entry.config.gcTime;
      }
    };
  }

  fetch(key: QueryKey, options: { force?: boolean } = {}): Promise<void> {
    const entry = this.entries.get(hashKey(key));
    if (!entry) {
      return Promise.resolve();
    }
    return this.run(entry, options.force ?? true);
  }

  seed<T>(key: QueryKey, data: T): void {
    const entry = this.entries.get(hashKey(key));
    if (!entry) {
      return;
    }
    this.patch(entry, {
      status: 'success',
      data,
      error: null,
      refetchError: null,
      dataUpdatedAt: Date.now(),
    });
  }

  setData<T>(key: QueryKey, updater: T | ((previous: T | undefined) => T | undefined)): void {
    const entry = this.entries.get(hashKey(key)) as Entry<T> | undefined;
    if (!entry) {
      return;
    }
    const next =
      typeof updater === 'function'
        ? (updater as (previous: T | undefined) => T | undefined)(entry.state.data)
        : updater;
    if (next === undefined) {
      return;
    }
    this.patch(entry, {
      status: 'success',
      data: next,
      error: null,
      refetchError: null,
      dataUpdatedAt: Date.now(),
    });
  }

  invalidate(prefix: QueryKey, options: { refetchActive?: boolean } = {}): Promise<void> {
    const refetchActive = options.refetchActive ?? true;
    const waiting: Promise<void>[] = [];
    for (const entry of this.entries.values()) {
      if (!matchesPrefix(entry.key, prefix)) {
        continue;
      }
      entry.invalidated = true;
      if (refetchActive && entry.subscribers.size > 0) {
        waiting.push(this.run(entry, true));
      }
    }
    return Promise.all(waiting).then(() => undefined);
  }

  remove(prefix: QueryKey): void {
    for (const [hash, entry] of [...this.entries.entries()]) {
      if (!matchesPrefix(entry.key, prefix)) {
        continue;
      }
      this.dispose(entry);
      this.entries.delete(hash);
    }
  }

  clear(): void {
    for (const entry of this.entries.values()) {
      this.dispose(entry);
    }
    this.entries.clear();
    this.unbindFocus();
  }

  applyOptimistic(patches: OptimisticPatch[]): { rollback: () => void; settle: () => void } {
    const captured: Array<{ entry: Entry; state: QueryState<unknown> }> = [];

    for (const patch of patches) {
      const entry = this.entries.get(hashKey(patch.key));
      if (!entry) {
        continue;
      }
      captured.push({ entry, state: entry.state });
      entry.optimisticDepth += 1;
      const next = patch.update(entry.state.data);
      if (next !== undefined) {
        this.patch(entry, { data: next });
      }
    }

    let settled = false;
    const release = () => {
      if (settled) {
        return;
      }
      settled = true;
      for (const item of captured) {
        item.entry.optimisticDepth = Math.max(0, item.entry.optimisticDepth - 1);
      }
    };

    return {
      rollback: () => {
        if (settled) {
          return;
        }
        for (const item of captured) {
          this.replace(item.entry, item.state);
        }
        release();
      },
      settle: release,
    };
  }

  inspect(): Array<{
    key: QueryKey;
    status: string;
    isFetching: boolean;
    subscribers: number;
    dataUpdatedAt: number;
  }> {
    return [...this.entries.values()].map((entry) => ({
      key: entry.key,
      status: entry.state.status,
      isFetching: entry.state.isFetching,
      subscribers: entry.subscribers.size,
      dataUpdatedAt: entry.state.dataUpdatedAt,
    }));
  }

  private ensure<T>(key: QueryKey, config: QueryConfig<T>): Entry<T> {
    const hash = hashKey(key);
    const existing = this.entries.get(hash) as Entry<T> | undefined;
    if (existing) {
      return existing;
    }
    const entry: Entry<T> = {
      key: parseKey(hash),
      config,
      state: IDLE_STATE as QueryState<T>,
      subscribers: new Set(),
      promise: null,
      controller: null,
      trailing: false,
      invalidated: false,
      optimisticDepth: 0,
      collectAt: null,
    };
    this.entries.set(hash, entry as Entry<unknown>);
    return entry;
  }

  private patch<T>(entry: Entry<T>, changes: Partial<QueryState<T>>): void {
    this.replace(entry, { ...entry.state, ...changes });
  }

  private replace<T>(entry: Entry<T>, next: QueryState<T>): void {
    const previous = entry.state;
    if (
      previous.status === next.status &&
      previous.data === next.data &&
      previous.error === next.error &&
      previous.refetchError === next.refetchError &&
      previous.isFetching === next.isFetching &&
      previous.dataUpdatedAt === next.dataUpdatedAt
    ) {
      return;
    }
    entry.state = Object.freeze(next);
    for (const listener of [...entry.subscribers]) {
      listener();
    }
  }

  private maybeFetch(entry: Entry): Promise<void> {
    if (entry.state.status === 'idle' || entry.invalidated) {
      return this.run(entry, false);
    }
    if (entry.state.status === 'error') {
      return Promise.resolve();
    }
    if (Date.now() - entry.state.dataUpdatedAt >= entry.config.staleTime) {
      return this.run(entry, false);
    }
    return Promise.resolve();
  }

  private run(entry: Entry, force: boolean): Promise<void> {
    if (entry.promise) {
      if (force) {
        entry.trailing = true;
      }
      return entry.promise;
    }

    const execute = async (): Promise<void> => {
      do {
        entry.trailing = false;
        entry.invalidated = false;
        const controller = new AbortController();
        entry.controller = controller;
        this.patch(entry, {
          isFetching: true,
          status: entry.state.status === 'idle' ? 'pending' : entry.state.status,
        });

        try {
          const data = await entry.config.fetcher({ signal: controller.signal });
          if (controller.signal.aborted) {
            return;
          }
          if (entry.optimisticDepth > 0) {
            entry.invalidated = true;
            continue;
          }
          this.patch(entry, {
            status: 'success',
            data,
            error: null,
            refetchError: null,
            dataUpdatedAt: Date.now(),
          });
        } catch (caught: unknown) {
          if (controller.signal.aborted || isAbortError(caught)) {
            return;
          }
          const described = describeError(caught, entry.config.fallbackMessage);
          if (entry.state.status === 'success' && entry.config.onRefetchError === 'keep') {
            this.patch(entry, { refetchError: described });
          } else {
            this.patch(entry, {
              status: 'error',
              data: undefined,
              error: described,
              refetchError: null,
            });
          }
        }
      } while (entry.trailing || entry.invalidated);

      this.patch(entry, { isFetching: false });
    };

    const promise = execute();
    entry.promise = promise;
    void promise.finally(() => {
      if (entry.promise === promise) {
        entry.promise = null;
        entry.controller = null;
      }
    });
    return promise;
  }

  private sweep(): void {
    const now = Date.now();
    for (const [hash, entry] of [...this.entries.entries()]) {
      if (entry.subscribers.size > 0 || entry.collectAt === null) {
        continue;
      }
      if (entry.collectAt <= now && entry.promise === null) {
        this.entries.delete(hash);
      }
    }
  }

  private dispose(entry: Entry): void {
    entry.controller?.abort();
    entry.subscribers.clear();
  }

  private bindFocus(): void {
    if (this.focusHandler !== null || typeof window === 'undefined') {
      return;
    }
    const handler = () => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
        return;
      }
      for (const entry of this.entries.values()) {
        if (entry.config.refetchOnFocus && entry.subscribers.size > 0) {
          void this.run(entry, true);
        }
      }
    };
    this.focusHandler = handler;
    window.addEventListener('focus', handler);
    document.addEventListener('visibilitychange', handler);
  }

  private unbindFocus(): void {
    if (this.focusHandler === null) {
      return;
    }
    window.removeEventListener('focus', this.focusHandler);
    document.removeEventListener('visibilitychange', this.focusHandler);
    this.focusHandler = null;
  }
}

export const queryCache = new QueryCache();

export function resetQueryCache(): void {
  queryCache.clear();
}
