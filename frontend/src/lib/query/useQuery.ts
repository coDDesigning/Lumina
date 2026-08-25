import { useCallback, useMemo, useRef, useSyncExternalStore } from 'react';
import type { DescribedError } from '@/api/errors';
import {
  DEFAULT_GC_TIME,
  IDLE_STATE,
  queryCache,
  type QueryConfig,
  type QueryState,
} from './cache';
import { hashKey, parseKey, type QueryKey } from './key';

export interface UseQueryOptions<T> {
  key: QueryKey | null;
  fetcher: (context: { signal: AbortSignal }) => Promise<T>;
  fallbackMessage: string;
  staleTime?: number;
  gcTime?: number;
  refetchOnFocus?: boolean;
  onRefetchError?: 'keep' | 'discard';
}

export interface QueryResult<T> {
  status: 'idle' | 'pending' | 'success' | 'error';
  data: T | undefined;
  error: DescribedError | null;
  refetchError: DescribedError | null;
  isFetching: boolean;
  refetch: () => Promise<void>;
}

export function useQuery<T>(options: UseQueryOptions<T>): QueryResult<T> {
  const fetcherRef = useRef(options.fetcher);
  fetcherRef.current = options.fetcher;

  const stableFetcher = useRef<QueryConfig<T>['fetcher'] | null>(null);
  if (stableFetcher.current === null) {
    stableFetcher.current = (context) => fetcherRef.current(context);
  }

  const configRef = useRef<QueryConfig<T> | null>(null);
  configRef.current = {
    fetcher: stableFetcher.current,
    fallbackMessage: options.fallbackMessage,
    staleTime: options.staleTime ?? 0,
    gcTime: options.gcTime ?? DEFAULT_GC_TIME,
    refetchOnFocus: options.refetchOnFocus ?? false,
    onRefetchError: options.onRefetchError ?? 'keep',
  };

  const hash = options.key === null ? null : hashKey(options.key);
  const key = useMemo<QueryKey | null>(() => (hash === null ? null : parseKey(hash)), [hash]);

  const subscribe = useCallback(
    (listener: () => void) => {
      if (key === null) {
        return () => undefined;
      }
      return queryCache.subscribe<T>(key, configRef.current as QueryConfig<T>, listener);
    },
    [key],
  );

  const getSnapshot = useCallback(
    () => (key === null ? (IDLE_STATE as QueryState<T>) : queryCache.getState<T>(key)),
    [key],
  );

  const state = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  const refetch = useCallback(
    () => (key === null ? Promise.resolve() : queryCache.fetch(key, { force: true })),
    [key],
  );

  return useMemo(
    () => ({
      status: state.status,
      data: state.data,
      error: state.error,
      refetchError: state.refetchError,
      isFetching: state.isFetching,
      refetch,
    }),
    [state, refetch],
  );
}
