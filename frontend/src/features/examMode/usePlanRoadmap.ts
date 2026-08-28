import { useMemo } from 'react';
import { generatedOutputsAPI } from '@/api/generatedOutputs';
import { queryKeys } from '@/api/queryKeys';
import type { ExamRoadmap, GeneratedOutputSummary } from '@/api/types';
import { useQuery } from '@/lib/query/useQuery';

/**
 * The newest saved roadmap for one plan, read back rather than remembered.
 *
 * A roadmap built and then navigated away from used to vanish, because it lived
 * only in component state -- so coming back offered to build it again, which is
 * a second version of work the student had already done. It is a stored output
 * like everything else here, so it reopens with no provider call.
 */
export function usePlanRoadmap(courseId: number, planId: number) {
  const outputs = useQuery<GeneratedOutputSummary[]>({
    key: queryKeys.courseOutputs(courseId),
    fetcher: ({ signal }) => generatedOutputsAPI.list(courseId, { signal }),
    fallbackMessage: 'Your saved plans could not be loaded.',
  });

  /**
   * Which stored roadmap belongs to this plan. `generation_settings` is read
   * permissively: a legacy row whose JSON no longer carries a plan is simply
   * not this plan's, rather than an error.
   */
  const outputId = useMemo(() => {
    const rows = (outputs.data ?? []).filter((row) => {
      if (row.output_type !== 'exam_roadmap') return false;
      const settings = row.generation_settings as { plan_output_id?: unknown } | null;
      return settings?.plan_output_id === planId;
    });
    if (rows.length === 0) return null;
    // Newest wins; older versions stay readable through their own ids.
    return rows.reduce((latest, row) => (row.id > latest.id ? row : latest)).id;
  }, [outputs.data, planId]);

  const detail = useQuery({
    key: outputId ? queryKeys.courseOutput(courseId, outputId) : null,
    fetcher: ({ signal }) => generatedOutputsAPI.get(courseId, outputId as number, { signal }),
    fallbackMessage: 'That roadmap could not be loaded.',
    staleTime: 5 * 60_000,
  });

  const roadmap = useMemo<ExamRoadmap | null>(() => {
    const content = detail.data?.content;
    if (!content || typeof content !== 'object') return null;
    const document = content as Partial<ExamRoadmap>;
    // A malformed legacy artifact fails its own renderer, never the page.
    return Array.isArray(document.days) ? (document as ExamRoadmap) : null;
  }, [detail.data]);

  return {
    roadmap,
    /** True only while a roadmap this plan owns is actually being fetched. */
    isLoading:
      outputs.status === 'pending' ||
      (outputId !== null && (detail.status === 'pending' || detail.status === 'idle')),
    reload: () => {
      void outputs.refetch();
      void detail.refetch();
    },
  };
}
