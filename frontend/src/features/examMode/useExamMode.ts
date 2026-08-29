import { useMemo } from 'react';
import { examModeAPI } from '@/api/examMode';
import { queryKeys } from '@/api/queryKeys';
import type { ExamAnalysisView, ExamPlanList, ExamSourceInventory } from '@/api/types';
import { useQuery } from '@/lib/query/useQuery';
import { deriveReadiness } from './examPrerequisites';
import type { ExamReadiness } from './examPrerequisites';

export interface ExamModeOverview {
  inventory: ExamSourceInventory | undefined;
  plans: ExamPlanList | undefined;
  analysis: ExamAnalysisView | undefined;
  /** Topic keys already paid for. Empty for a support reader, who never reads it. */
  unlockedTopicKeys: ReadonlySet<string>;
  readiness: ExamReadiness | null;
  isLoading: boolean;
  /** Set only when the course-level reads failed, never for an absent analysis. */
  error: string | null;
  hasAnalysis: boolean;
  reload: () => void;
}

/**
 * Everything the Exam Mode overview reads, and nothing it writes.
 *
 * The analysis read is allowed to fail without failing the screen: a course
 * that has never been analysed answers 404, and that is an empty state rather
 * than an error. The two course-level reads are different -- if the inventory
 * or the plan list cannot be loaded, the screen cannot honestly claim anything.
 */
export function useExamMode(courseId: number, options: { readOnly?: boolean } = {}) {
  const sources = useQuery<ExamSourceInventory>({
    key: queryKeys.examSources(courseId),
    fetcher: ({ signal }) => examModeAPI.listSources(courseId, { signal }),
    fallbackMessage: 'Your exam sources could not be loaded.',
  });

  const plans = useQuery<ExamPlanList>({
    key: queryKeys.examPlans(courseId),
    fetcher: ({ signal }) => examModeAPI.listPlans(courseId, { signal }),
    fallbackMessage: 'Your saved exam plans could not be loaded.',
  });

  const analysis = useQuery<ExamAnalysisView>({
    key: queryKeys.examAnalysis(courseId, null),
    fetcher: ({ signal }) => examModeAPI.getAnalysis(courseId, null, { signal }),
    fallbackMessage: 'The topic analysis could not be loaded.',
  });

  // Owner-scoped on the server, so a support reader must never ask for it.
  const entitlements = useQuery({
    key: options.readOnly ? null : queryKeys.examEntitlements(courseId),
    fetcher: ({ signal }) => examModeAPI.listEntitlements(courseId, { signal }),
    fallbackMessage: 'Your unlocked topics could not be loaded.',
  });

  const unlockedTopicKeys = useMemo(
    () => new Set(entitlements.data?.unlocked_topic_keys ?? []),
    [entitlements.data],
  );

  return {
    sources,
    plans,
    analysisQuery: analysis,
    analysis: analysis.status === 'success' ? analysis.data : undefined,
    unlockedTopicKeys,
    hasAnalysis: analysis.status === 'success' && analysis.data !== undefined,
    isLoading:
      sources.status === 'pending' ||
      sources.status === 'idle' ||
      plans.status === 'pending' ||
      plans.status === 'idle',
    error: sources.error?.message ?? plans.error?.message ?? null,
    reload: () => {
      void sources.refetch();
      void plans.refetch();
      void analysis.refetch();
    },
  };
}

export interface ReadinessInput {
  inventory: ExamSourceInventory | undefined;
  examDate: string;
  planCount: number;
  today?: Date;
}

export function useReadiness({
  inventory,
  examDate,
  planCount,
  today,
}: ReadinessInput): ExamReadiness | null {
  return useMemo(() => {
    if (!inventory) return null;
    return deriveReadiness({
      inventory,
      examDate: examDate || null,
      hasPlan: planCount > 0,
      today: today ?? new Date(),
    });
  }, [inventory, examDate, planCount, today]);
}
