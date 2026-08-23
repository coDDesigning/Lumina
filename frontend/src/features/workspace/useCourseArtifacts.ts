import { useCallback, useEffect, useState } from 'react';
import { isAbortError } from '@/api/errors';
import { generatedOutputsAPI } from '@/api/generatedOutputs';
import type { CourseProgressResponse } from '@/api/types';

export type CourseArtifact =
  | {
      kind: 'study_guide' | 'flashcards' | 'other';
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
  reload: () => void;
}

const MEANINGFUL_TOPIC = (topic: string | null | undefined): string | null => {
  if (!topic || topic.toLowerCase() === 'all topics') {
    return null;
  }
  return topic;
};

function outputKind(outputType: string): 'study_guide' | 'flashcards' | 'other' {
  if (outputType === 'study_guide' || outputType === 'flashcards') {
    return outputType;
  }
  return 'other';
}

export function useCourseArtifacts(
  courseId: number,
  progress: CourseProgressResponse | null,
): CourseArtifactsState {
  const [outputs, setOutputs] = useState<CourseArtifact[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [token, setToken] = useState(0);

  const reload = useCallback(() => setToken((current) => current + 1), []);

  useEffect(() => {
    if (!Number.isInteger(courseId) || courseId <= 0) {
      return;
    }

    const controller = new AbortController();
    let cancelled = false;

    generatedOutputsAPI
      .list(courseId, { signal: controller.signal })
      .then((rows) => {
        if (cancelled) {
          return;
        }
        setOutputs(
          rows.map((row) => ({
            kind: outputKind(row.output_type),
            key: `output-${row.id}`,
            outputId: row.id,
            outputType: row.output_type,
            topic: MEANINGFUL_TOPIC(row.generation_settings?.topic_focus),
            createdAt: row.created_at,
          })),
        );
        setIsLoading(false);
      })
      .catch((caught: unknown) => {
        if (cancelled || isAbortError(caught)) {
          return;
        }
        setOutputs([]);
        setIsLoading(false);
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [courseId, token]);

  const attempts: CourseArtifact[] = (progress?.quiz_history ?? []).map((item) => ({
    kind: 'quiz',
    key: `attempt-${item.attempt_id}`,
    score: item.score,
    correctCount: item.correct_count,
    totalQuestions: item.total_questions,
    createdAt: item.created_at,
  }));

  const artifacts = [...outputs, ...attempts].sort(
    (a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt),
  );

  return { artifacts, isLoading, reload };
}
