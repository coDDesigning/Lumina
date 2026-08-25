import { useCallback, useEffect, useRef, useState } from 'react';
import { describeError, type DescribedError } from '@/api/errors';
import { queryCache, type OptimisticPatch } from './cache';
import type { QueryKey } from './key';

export function patchQuery<T>(
  key: QueryKey,
  update: (previous: T | undefined) => T | undefined,
): OptimisticPatch {
  return {
    key,
    update: (previous) => update(previous as T | undefined),
  };
}

export interface UseMutationOptions<TVars, TData> {
  mutate: (vars: TVars, context: { signal: AbortSignal }) => Promise<TData>;
  fallbackMessage: string;
  optimistic?: (vars: TVars) => OptimisticPatch[];
  invalidates?: (vars: TVars, data: TData) => QueryKey[];
  onSuccess?: (data: TData, vars: TVars) => void;
}

export interface MutationResult<TVars, TData> {
  run: (vars: TVars) => Promise<TData>;
  isPending: boolean;
  error: DescribedError | null;
  reset: () => void;
}

export function useMutation<TVars, TData>(
  options: UseMutationOptions<TVars, TData>,
): MutationResult<TVars, TData> {
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const isMounted = useRef(true);
  useEffect(() => {
    isMounted.current = true;
    return () => {
      isMounted.current = false;
    };
  }, []);

  const [isPending, setIsPending] = useState(false);
  const [error, setError] = useState<DescribedError | null>(null);

  const run = useCallback(async (vars: TVars): Promise<TData> => {
    const current = optionsRef.current;
    const controller = new AbortController();
    const optimistic = current.optimistic
      ? queryCache.applyOptimistic(current.optimistic(vars))
      : null;

    if (isMounted.current) {
      setIsPending(true);
      setError(null);
    }

    try {
      const data = await current.mutate(vars, { signal: controller.signal });
      optimistic?.settle();
      current.onSuccess?.(data, vars);
      for (const key of current.invalidates?.(vars, data) ?? []) {
        void queryCache.invalidate(key);
      }
      if (isMounted.current) {
        setIsPending(false);
      }
      return data;
    } catch (caught: unknown) {
      optimistic?.rollback();
      if (isMounted.current) {
        setIsPending(false);
        setError(describeError(caught, current.fallbackMessage));
      }
      throw caught;
    }
  }, []);

  const reset = useCallback(() => {
    setIsPending(false);
    setError(null);
  }, []);

  return { run, isPending, error, reset };
}
