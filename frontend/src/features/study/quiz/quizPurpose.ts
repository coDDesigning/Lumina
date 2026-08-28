import type { QuizPurpose, QuizView } from '@/api/types';

/**
 * What a quiz is called and where handing it in returns to.
 *
 * Read from `quiz_purpose`, never inferred from a title: a title is text a
 * model wrote and a student can see, so branching on it would make the return
 * link depend on wording.
 */
const LABELS: Record<QuizPurpose, string> = {
  practice: 'Practice quiz',
  exam_topic_practice: 'Topic practice',
  exam_topic_exam: 'Topic exam',
  exam_similar_questions: 'Similar-question set',
  exam_mock_exam: 'Mock exam',
};

export function purposeLabel(purpose: string | null | undefined): string {
  if (!purpose) return LABELS.practice;
  return LABELS[purpose as QuizPurpose] ?? LABELS.practice;
}

export interface QuizReturn {
  label: string;
  to: string;
}

/**
 * Where a quiz belongs, so finishing one puts the student back in the workflow
 * they left rather than at the course root every time.
 *
 * A quiz that names a plan and a topic returns to that topic; one that names
 * only a plan returns to the plan; anything else returns to the course.
 */
export function quizReturn(courseId: number, quiz: Pick<
  QuizView,
  'quiz_purpose' | 'exam_plan_output_id' | 'exam_topic_key'
>): QuizReturn {
  const { exam_plan_output_id: planId, exam_topic_key: topicKey } = quiz;

  if (planId && topicKey) {
    return {
      label: 'Back to the topic',
      to: `/courses/${courseId}/exam-mode/plans/${planId}/topics/${encodeURIComponent(topicKey)}`,
    };
  }
  if (planId) {
    return { label: 'Back to the exam plan', to: `/courses/${courseId}/exam-mode/plans/${planId}` };
  }
  return { label: 'Back to the course', to: `/courses/${courseId}` };
}

/** A paper sat against a clock, which the ordinary attempt endpoint refuses. */
export function isTimed(quiz: Pick<QuizView, 'timed' | 'time_limit_seconds'>): boolean {
  return Boolean(quiz.timed || (quiz.time_limit_seconds && quiz.time_limit_seconds > 0));
}
