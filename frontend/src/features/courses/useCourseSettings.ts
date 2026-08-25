import { queryKeys } from '@/api/queryKeys';
import { settingsAPI } from '@/api/settings';
import type { CourseSettings } from '@/api/types';
import { useQuery, type QueryResult } from '@/lib/query/useQuery';

export function useCourseSettings(courseId: number): QueryResult<CourseSettings> {
  const isValid = Number.isInteger(courseId) && courseId > 0;

  return useQuery<CourseSettings>({
    key: isValid ? queryKeys.courseSettings(courseId) : null,
    fetcher: ({ signal }) => settingsAPI.get(courseId, { signal }),
    fallbackMessage: "This course's defaults could not be loaded.",
    staleTime: 5 * 60_000,
  });
}
