import { useCallback, useEffect, useState } from 'react';
import { describeError, isAbortError } from '@/api/errors';
import { progressAPI } from '@/api/progress';
import type { CourseProgressResponse } from '@/api/types';

export interface CourseProgressState {
  progress: CourseProgressResponse | null;
  isLoading: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * Course progress, shared by the workspace (which reports the average score
 * back to the course list) and the progress page.
 */
export function useCourseProgress(courseId: number): CourseProgressState {
  const [progress, setProgress] = useState<CourseProgressResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState(0);

  const reload = useCallback(() => {
    setToken((current) => current + 1);
  }, []);

  useEffect(() => {
    if (!Number.isInteger(courseId) || courseId <= 0) {
      return;
    }

    const controller = new AbortController();
    let cancelled = false;

    setIsLoading(true);
    setError(null);

    progressAPI
      .get(courseId, { signal: controller.signal })
      .then((result) => {
        if (cancelled) return;
        setProgress(result);
        setIsLoading(false);
      })
      .catch((caught: unknown) => {
        if (cancelled || isAbortError(caught)) return;
        setIsLoading(false);
        setError(describeError(caught, 'Progress could not be loaded.').message);
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [courseId, token]);

  return { progress, isLoading, error, reload };
}
