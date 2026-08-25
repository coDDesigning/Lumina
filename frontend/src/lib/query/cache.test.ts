import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { QueryCache, type QueryConfig } from './cache';
import { matchesPrefix } from './key';

function config<T>(
  fetcher: QueryConfig<T>['fetcher'],
  overrides: Partial<QueryConfig<T>> = {},
): QueryConfig<T> {
  return {
    fetcher,
    fallbackMessage: 'It could not be loaded.',
    staleTime: 0,
    gcTime: 60_000,
    refetchOnFocus: false,
    onRefetchError: 'keep',
    ...overrides,
  };
}

function settle(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function apiError(message: string, status: number): APIError {
  return new APIError(status, { detail: message });
}

describe('matchesPrefix', () => {
  it('does not treat course 1 as a prefix of course 11', () => {
    expect(matchesPrefix(['course', 11, 'documents'], ['course', 1])).toBe(false);
    expect(matchesPrefix(['course', 1, 'documents'], ['course', 1])).toBe(true);
  });

  it('does not match a longer prefix than the key', () => {
    expect(matchesPrefix(['course', 1], ['course', 1, 'documents'])).toBe(false);
  });

  it('keeps the course list separate from a single course', () => {
    expect(matchesPrefix(['courses'], ['course', 1])).toBe(false);
    expect(matchesPrefix(['course', 1, 'progress'], ['courses'])).toBe(false);
  });

  it('matches every segment of a course', () => {
    const prefix = ['course', 7];
    expect(matchesPrefix(['course', 7, 'documents'], prefix)).toBe(true);
    expect(matchesPrefix(['course', 7, 'quiz', 3, 'attempts'], prefix)).toBe(true);
  });
});

describe('QueryCache', () => {
  let cache: QueryCache;

  beforeEach(() => {
    cache = new QueryCache();
  });

  afterEach(() => {
    cache.clear();
  });

  it('reports idle for an unknown key', () => {
    expect(cache.getState(['nothing'])).toMatchObject({
      status: 'idle',
      data: undefined,
      error: null,
      isFetching: false,
    });
  });

  it('returns the identical snapshot object across calls', () => {
    expect(cache.getState(['a'])).toBe(cache.getState(['b']));
  });

  it('deduplicates concurrent readers of one key', async () => {
    const control = deferred<string>();
    const fetcher = vi.fn(() => control.promise);
    cache.subscribe(['thing'], config(fetcher), () => {});

    void cache.fetch(['thing'], { force: false });
    void cache.fetch(['thing'], { force: false });
    control.resolve('done');
    await settle();

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(cache.getState(['thing']).data).toBe('done');
  });

  it('runs one trailing refresh when invalidated mid-flight', async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const fetcher = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    cache.subscribe(['thing'], config(fetcher), () => {});

    void cache.invalidate(['thing']);
    first.resolve('stale');
    second.resolve('fresh');
    await settle();

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(cache.getState(['thing']).data).toBe('fresh');
  });

  it('honours staleTime across an unsubscribe and resubscribe', async () => {
    const fetcher = vi.fn().mockResolvedValue('value');
    const options = config(fetcher, { staleTime: 60_000 });

    const first = cache.subscribe(['thing'], options, () => {});
    await settle();
    first();

    cache.subscribe(['thing'], options, () => {});
    await settle();

    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('does not fetch an invalidated key that nobody is watching', async () => {
    const fetcher = vi.fn().mockResolvedValue('value');
    const unsubscribe = cache.subscribe(['thing'], config(fetcher), () => {});
    await settle();
    unsubscribe();

    await cache.invalidate(['thing']);
    expect(fetcher).toHaveBeenCalledTimes(1);

    cache.subscribe(['thing'], config(fetcher), () => {});
    await settle();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('invalidates only the course it was given', async () => {
    const one = vi.fn().mockResolvedValue('one');
    const eleven = vi.fn().mockResolvedValue('eleven');
    cache.subscribe(['course', 1, 'documents'], config(one), () => {});
    cache.subscribe(['course', 11, 'documents'], config(eleven), () => {});
    await settle();

    await cache.invalidate(['course', 1]);

    expect(one).toHaveBeenCalledTimes(2);
    expect(eleven).toHaveBeenCalledTimes(1);
  });

  it('separates a failed load from an empty result', async () => {
    const fetcher = vi.fn().mockRejectedValue(apiError('Gone', 404));
    cache.subscribe(['thing'], config(fetcher), () => {});
    await settle();

    const state = cache.getState(['thing']);
    expect(state.status).toBe('error');
    expect(state.data).toBeUndefined();
    expect(state.error?.message).toBe('Gone');
  });

  it('keeps data on screen when a background refresh fails', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(['a'])
      .mockRejectedValueOnce(apiError('Boom', 500));
    cache.subscribe(['thing'], config(fetcher), () => {});
    await settle();

    await cache.fetch(['thing']);

    const state = cache.getState<string[]>(['thing']);
    expect(state.status).toBe('success');
    expect(state.data).toEqual(['a']);
    expect(state.refetchError?.message).toBe('Boom');
  });

  it('discards the previous value when told to', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(['a'])
      .mockRejectedValueOnce(apiError('Boom', 500));
    cache.subscribe(['thing'], config(fetcher, { onRefetchError: 'discard' }), () => {});
    await settle();

    await cache.fetch(['thing']);

    const state = cache.getState<string[]>(['thing']);
    expect(state.status).toBe('error');
    expect(state.data).toBeUndefined();
  });

  it('does not retry a failed entry when a new subscriber arrives', async () => {
    const fetcher = vi.fn().mockRejectedValue(apiError('Boom', 500));
    cache.subscribe(['thing'], config(fetcher), () => {});
    await settle();

    cache.subscribe(['thing'], config(fetcher), () => {});
    await settle();

    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('ignores an abort rather than reporting it as a failure', async () => {
    const fetcher = vi.fn().mockRejectedValue(new DOMException('Aborted', 'AbortError'));
    cache.subscribe(['thing'], config(fetcher), () => {});
    await settle();

    expect(cache.getState(['thing']).error).toBeNull();
  });

  it('rolls an optimistic patch back to the exact prior state', async () => {
    const fetcher = vi.fn().mockResolvedValue(['a', 'b']);
    cache.subscribe(['thing'], config(fetcher), () => {});
    await settle();
    const before = cache.getState<string[]>(['thing']);

    const optimistic = cache.applyOptimistic([
      { key: ['thing'], update: (previous) => (previous as string[]).filter((v) => v !== 'a') },
    ]);
    expect(cache.getState<string[]>(['thing']).data).toEqual(['b']);

    optimistic.rollback();
    expect(cache.getState<string[]>(['thing'])).toEqual(before);
  });

  it('discards a fetch that resolves while an optimistic patch is open', async () => {
    const first = deferred<string[]>();
    const second = deferred<string[]>();
    const fetcher = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    cache.subscribe(['thing'], config(fetcher), () => {});

    const optimistic = cache.applyOptimistic([{ key: ['thing'], update: () => ['optimistic'] }]);
    first.resolve(['server']);
    await Promise.resolve();
    optimistic.settle();
    second.resolve(['settled']);
    await settle();

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(cache.getState<string[]>(['thing']).data).toEqual(['settled']);
  });

  it('collects an abandoned entry on the next subscribe', async () => {
    const fetcher = vi.fn().mockResolvedValue('value');
    const unsubscribe = cache.subscribe(['stale'], config(fetcher, { gcTime: 0 }), () => {});
    await settle();
    unsubscribe();

    expect(cache.inspect()).toHaveLength(1);

    cache.subscribe(['other'], config(vi.fn().mockResolvedValue('x')), () => {});
    await settle();

    expect(cache.inspect().map((entry) => entry.key)).toEqual([['other']]);
  });

  it('keeps an entry whose collection window has not elapsed', async () => {
    const fetcher = vi.fn().mockResolvedValue('value');
    const unsubscribe = cache.subscribe(['thing'], config(fetcher, { gcTime: 60_000 }), () => {});
    await settle();
    unsubscribe();

    cache.subscribe(['other'], config(vi.fn().mockResolvedValue('x')), () => {});
    await settle();

    expect(cache.inspect()).toHaveLength(2);
  });

  it('keeps a resubscribed entry and its data', async () => {
    const fetcher = vi.fn().mockResolvedValue('value');
    const options = config(fetcher, { gcTime: 0, staleTime: 60_000 });
    const unsubscribe = cache.subscribe(['thing'], options, () => {});
    await settle();
    unsubscribe();

    cache.subscribe(['thing'], options, () => {});
    await settle();

    expect(cache.getState(['thing']).data).toBe('value');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('creates no timers of its own', async () => {
    vi.useFakeTimers();
    try {
      const fetcher = vi.fn().mockResolvedValue('value');
      const unsubscribe = cache.subscribe(['thing'], config(fetcher), () => {});
      await vi.advanceTimersByTimeAsync(0);
      unsubscribe();

      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it('removes an entry outright rather than marking it stale', async () => {
    const fetcher = vi.fn().mockResolvedValue('value');
    cache.subscribe(['course', 4, 'documents'], config(fetcher), () => {});
    await settle();

    cache.remove(['course', 4]);
    expect(cache.inspect()).toHaveLength(0);
  });

  it('notifies subscribers when data lands', async () => {
    const listener = vi.fn();
    const fetcher = vi.fn().mockResolvedValue('value');
    cache.subscribe(['thing'], config(fetcher), listener);
    await settle();

    expect(listener).toHaveBeenCalled();
  });

  it('stops notifying after unsubscribe', async () => {
    const listener = vi.fn();
    const fetcher = vi.fn().mockResolvedValue('value');
    const unsubscribe = cache.subscribe(['thing'], config(fetcher), listener);
    unsubscribe();
    listener.mockClear();

    await cache.fetch(['thing']);
    expect(listener).not.toHaveBeenCalled();
  });

  it('ignores setData for a key nothing has cached', () => {
    cache.setData(['missing'], ['value']);
    expect(cache.getState(['missing']).status).toBe('idle');
  });
});
