import { beforeEach, describe, expect, it, vi } from 'vitest';
import { queryCache, type QueryConfig } from '@/lib/query/cache';
import type { QueryKey } from '@/lib/query/key';
import {
  afterConversationTurn,
  afterCourseDeleted,
  afterDocumentChanged,
  afterQuizAttempt,
  afterQuizGenerated,
} from './invalidations';
import { queryKeys } from './queryKeys';

function config(fetcher: () => Promise<string>): QueryConfig<string> {
  return {
    fetcher,
    fallbackMessage: 'It could not be loaded.',
    staleTime: 60_000,
    gcTime: 60_000,
    refetchOnFocus: false,
    onRefetchError: 'keep',
  };
}

function settle(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

async function watch(key: QueryKey) {
  const fetcher = vi.fn().mockResolvedValue('value');
  queryCache.subscribe(key, config(fetcher), () => {});
  await settle();
  fetcher.mockClear();
  return fetcher;
}

describe('course-scoped invalidation', () => {
  beforeEach(() => {
    queryCache.clear();
  });

  it('refreshes the artifact rail and the quiz library when a quiz is generated', async () => {
    const outputs = await watch(queryKeys.courseOutputs(4));
    const quizzes = await watch(queryKeys.courseQuizzes(4));
    const activity = await watch(queryKeys.activity(5));

    afterQuizGenerated(4);
    await settle();

    expect(outputs).toHaveBeenCalledTimes(1);
    expect(quizzes).toHaveBeenCalledTimes(1);
    expect(activity).toHaveBeenCalledTimes(1);
  });

  it('refreshes progress, the quiz library and activity when an attempt is handed in', async () => {
    const progress = await watch(queryKeys.courseProgress(4));
    const allProgress = await watch(queryKeys.coursesProgress());
    const quizzes = await watch(queryKeys.courseQuizzes(4));
    const activity = await watch(queryKeys.activity(null));

    afterQuizAttempt(4);
    await settle();

    expect(progress).toHaveBeenCalledTimes(1);
    expect(allProgress).toHaveBeenCalledTimes(1);
    expect(quizzes).toHaveBeenCalledTimes(1);
    expect(activity).toHaveBeenCalledTimes(1);
  });

  it('refreshes activity when a question is answered', async () => {
    const activity = await watch(queryKeys.activity(20));
    const conversations = await watch(queryKeys.courseConversations(4));

    afterConversationTurn(4);
    await settle();

    expect(activity).toHaveBeenCalledTimes(1);
    expect(conversations).toHaveBeenCalledTimes(1);
  });

  it('reaches every activity list whatever limit it asked for', async () => {
    const short = await watch(queryKeys.activity(5));
    const long = await watch(queryKeys.activity(50));

    afterQuizAttempt(4);
    await settle();

    expect(short).toHaveBeenCalledTimes(1);
    expect(long).toHaveBeenCalledTimes(1);
  });

  it('never touches another course', async () => {
    const mine = await watch(queryKeys.courseDocuments(1));
    const theirs = await watch(queryKeys.courseDocuments(11));

    afterDocumentChanged(1);
    await settle();

    expect(mine).toHaveBeenCalledTimes(1);
    expect(theirs).not.toHaveBeenCalled();
  });

  it('drops a deleted course rather than refetching it', async () => {
    await watch(queryKeys.courseDocuments(9));
    await watch(queryKeys.courseProgress(9));

    afterCourseDeleted(9);
    await settle();

    expect(queryCache.getState(queryKeys.courseDocuments(9)).status).toBe('idle');
    expect(queryCache.getState(queryKeys.courseProgress(9)).status).toBe('idle');
  });
});
