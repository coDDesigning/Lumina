import { useEffect, useMemo } from 'react';
import { coursesAPI } from '@/api/courses';
import { queryKeys } from '@/api/queryKeys';
import type { Course } from '@/api/types';
import type { Workspace } from '@/data/workspaces';
import { useQuery } from '@/lib/query/useQuery';

export interface ResolvedCourse {
  workspace: Workspace | null;
  isLoading: boolean;
  isNotFound: boolean;
  error: string | null;
  retry: () => void;
}

/**
 * Resolve one course for a course-scoped route.
 *
 * The read is keyed off this course alone and never off the course list, so a
 * link opened cold resolves on its own: a list still in flight -- or one that
 * failed outright -- cannot decide whether this page loads.
 *
 * A 404 is the only status that means "not available", because a course that
 * was deleted and a course belonging to someone else answer alike. Everything
 * else -- offline, a timeout, a 500 -- is a failure to load and keeps a retry,
 * rather than bouncing a reader to the dashboard with nothing said.
 */
export function useCourseRoute(
  courseId: string | undefined,
  workspaces: Workspace[],
  toWorkspace: (course: Course) => Workspace,
  onSelect?: (courseId: string) => void,
): ResolvedCourse {
  const numericId = Number(courseId);
  const isNumeric = Number.isInteger(numericId) && numericId > 0;
  const listed = workspaces.find(({ id }) => id === courseId) ?? null;

  const courseQuery = useQuery<Course>({
    key: isNumeric ? queryKeys.course(numericId) : null,
    fetcher: ({ signal }) => coursesAPI.get(numericId, { signal }),
    fallbackMessage: 'Course could not be loaded.',
  });

  const workspace = useMemo(() => {
    if (listed) return listed;
    if (courseQuery.data) return toWorkspace(courseQuery.data);
    return null;
  }, [listed, courseQuery.data, toWorkspace]);

  useEffect(() => {
    if (workspace && onSelect) onSelect(workspace.id);
  }, [onSelect, workspace]);

  const failed = courseQuery.status === 'error' ? courseQuery.error : null;
  const refetch = courseQuery.refetch;

  return {
    workspace,
    isLoading:
      !workspace &&
      isNumeric &&
      (courseQuery.status === 'pending' || courseQuery.status === 'idle'),
    isNotFound: !workspace && (!isNumeric || failed?.status === 404),
    error: !workspace && failed && failed.status !== 404 ? failed.message : null,
    retry: () => {
      void refetch();
    },
  };
}
