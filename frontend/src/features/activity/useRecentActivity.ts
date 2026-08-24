import { useCallback, useEffect, useState } from 'react';
import { activityAPI } from '@/api/activity';
import { describeError, isAbortError } from '@/api/errors';
import type { ActivityItem } from '@/api/types';

export interface RecentActivityState {
  items: ActivityItem[];
  isLoading: boolean;
  error: string | null;
  reload: () => void;
}

export function useRecentActivity(limit?: number): RecentActivityState {
  const [items, setItems] = useState<ActivityItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState(0);

  const reload = useCallback(() => setToken((current) => current + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    setIsLoading(true);
    setError(null);

    activityAPI
      .list(limit, { signal: controller.signal })
      .then((result) => {
        if (cancelled) {
          return;
        }
        setItems(result);
        setIsLoading(false);
      })
      .catch((caught: unknown) => {
        if (cancelled || isAbortError(caught)) {
          return;
        }
        setIsLoading(false);
        setError(describeError(caught, 'Recent activity could not be loaded.').message);
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [limit, token]);

  return { items, isLoading, error, reload };
}
