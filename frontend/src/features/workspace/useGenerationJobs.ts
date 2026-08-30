import { useCallback, useEffect, useRef, useState } from 'react';
import { generationJobsAPI } from '@/api/generationJobs';
import {
  afterFlashcardsGenerated,
  afterQuizGenerated,
  afterStudyGuideGenerated,
} from '@/api/invalidations';
import { queryKeys } from '@/api/queryKeys';
import type { GenerationJob } from '@/api/types';
import { useCredits } from '@/context/CreditContext';
import { useQuery } from '@/lib/query/useQuery';

const POLL_INTERVAL_MS = 2000;
const NO_JOBS: GenerationJob[] = [];

export function useGenerationJobs(courseId: number) {
  const [retryingId, setRetryingId] = useState<number | null>(null);
  const completed = useRef(new Set<number>());
  const { refresh: refreshCredits } = useCredits();
  const query = useQuery<GenerationJob[]>({
    key: queryKeys.courseGenerationJobs(courseId),
    fetcher: ({ signal }) => generationJobsAPI.list(courseId, { signal }),
    fallbackMessage: 'Generation status could not be loaded.',
    onRefetchError: 'keep',
  });
  const jobs = query.data ?? NO_JOBS;
  const refetch = query.refetch;
  const hasActiveJobs = jobs.some((job) => job.status === 'queued' || job.status === 'running');

  useEffect(() => {
    if (!hasActiveJobs) return;
    const timer = setTimeout(() => void refetch(), POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [hasActiveJobs, jobs, refetch]);

  useEffect(() => {
    for (const job of jobs) {
      if (job.status !== 'succeeded' || completed.current.has(job.id)) continue;
      completed.current.add(job.id);
      if (job.job_type === 'generate_quiz') {
        afterQuizGenerated(courseId);
      } else if (job.job_type === 'generate_flashcard') {
        afterFlashcardsGenerated(courseId);
      } else {
        afterStudyGuideGenerated(courseId);
      }
      void refreshCredits();
    }
  }, [courseId, jobs, refreshCredits]);

  const retry = useCallback(
    async (jobId: number) => {
      setRetryingId(jobId);
      try {
        await generationJobsAPI.retry(courseId, jobId);
        await refreshCredits();
        await refetch();
      } finally {
        setRetryingId(null);
      }
    },
    [courseId, refetch, refreshCredits],
  );

  return {
    jobs,
    isLoading: query.status === 'pending' || query.status === 'idle',
    error: query.status === 'error' ? query.error?.message ?? 'Generation status is unavailable.' : null,
    refetchError: query.refetchError?.message ?? null,
    reload: refetch,
    retry,
    retryingId,
  };
}
