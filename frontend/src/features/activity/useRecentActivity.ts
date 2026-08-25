import { activityAPI } from '@/api/activity';
import { queryKeys } from '@/api/queryKeys';
import type { ActivityItem } from '@/api/types';
import { useQuery } from '@/lib/query/useQuery';

export interface RecentActivityState {
  items: ActivityItem[];
  isLoading: boolean;
  error: string | null;
  reload: () => void;
}

export function useRecentActivity(limit?: number): RecentActivityState {
  const query = useQuery<ActivityItem[]>({
    key: queryKeys.activity(limit ?? null),
    fetcher: ({ signal }) => activityAPI.list(limit, { signal }),
    fallbackMessage: 'Recent activity could not be loaded.',
    refetchOnFocus: true,
  });

  return {
    items: query.data ?? [],
    isLoading: query.status === 'pending' || query.status === 'idle',
    error: query.error?.message ?? null,
    reload: () => {
      void query.refetch();
    },
  };
}
