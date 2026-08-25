import { progressAPI } from '@/api/progress';
import { queryKeys } from '@/api/queryKeys';
import type { CourseProgressResponse } from '@/api/types';
import { useQuery } from '@/lib/query/useQuery';

export interface CourseProgressState {
  progress: CourseProgressResponse | null;
  isLoading: boolean;
  error: string | null;
  reload: () => void;
}

export function useCourseProgress(courseId: number): CourseProgressState {
  const isValid = Number.isInteger(courseId) && courseId > 0;

  const query = useQuery<CourseProgressResponse>({
    key: isValid ? queryKeys.courseProgress(courseId) : null,
    fetcher: ({ signal }) => progressAPI.get(courseId, { signal }),
    fallbackMessage: 'Progress could not be loaded.',
    staleTime: 30_000,
  });

  return {
    progress: query.data ?? null,
    isLoading: query.status === 'pending',
    error: query.error?.message ?? null,
    reload: () => {
      void query.refetch();
    },
  };
}
