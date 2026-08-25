import { useMemo } from 'react';
import { generatedOutputsAPI } from '@/api/generatedOutputs';
import { queryKeys } from '@/api/queryKeys';
import type { CourseProgressResponse, GeneratedOutputSummary } from '@/api/types';
import { useQuery } from '@/lib/query/useQuery';

export type CourseArtifact =
  | {
      kind: 'study_guide' | 'flashcards' | 'quiz' | 'other';
      key: string;
      outputId: number;
      outputType: string;
      topic: string | null;
      createdAt: string;
    }
  | {
      kind: 'quiz';
      key: string;
      score: number;
      correctCount: number;
      totalQuestions: number;
      createdAt: string;
    };

export type SavedArtifact = Extract<CourseArtifact, { outputId: number }>;

export interface CourseArtifactsState {
  artifacts: CourseArtifact[];
  isLoading: boolean;
  error: string | null;
  reload: () => void;
}

const MEANINGFUL_TOPIC = (topic: string | null | undefined): string | null => {
  if (!topic || topic.toLowerCase() === 'all topics') {
    return null;
  }
  return topic;
};

function outputKind(outputType: string): 'study_guide' | 'flashcards' | 'quiz' | 'other' {
  if (outputType === 'study_guide' || outputType === 'flashcards' || outputType === 'quiz') {
    return outputType;
  }
  return 'other';
}

export function useCourseArtifacts(
  courseId: number,
  progress: CourseProgressResponse | null,
): CourseArtifactsState {
  const isValid = Number.isInteger(courseId) && courseId > 0;

  const query = useQuery<GeneratedOutputSummary[]>({
    key: isValid ? queryKeys.courseOutputs(courseId) : null,
    fetcher: ({ signal }) => generatedOutputsAPI.list(courseId, { signal }),
    fallbackMessage: 'What you made could not be loaded.',
    staleTime: 30_000,
  });

  const rows = query.data;
  const history = progress?.quiz_history;

  const artifacts = useMemo(() => {
    const outputs: CourseArtifact[] = (rows ?? []).map((row) => ({
      kind: outputKind(row.output_type),
      key: `output-${row.id}`,
      outputId: row.id,
      outputType: row.output_type,
      topic: MEANINGFUL_TOPIC(row.generation_settings?.topic_focus),
      createdAt: row.created_at,
    }));

    const attempts: CourseArtifact[] = (history ?? []).map((item) => ({
      kind: 'quiz',
      key: `attempt-${item.attempt_id}`,
      score: item.score,
      correctCount: item.correct_count,
      totalQuestions: item.total_questions,
      createdAt: item.created_at,
    }));

    return [...outputs, ...attempts].sort(
      (a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt),
    );
  }, [rows, history]);

  return {
    artifacts,
    isLoading: query.status === 'pending' || query.status === 'idle',
    error: query.error?.message ?? null,
    reload: () => {
      void query.refetch();
    },
  };
}
