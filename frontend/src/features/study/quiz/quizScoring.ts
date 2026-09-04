import type { QuizAnswerResult, QuizAttemptResponse, QuizQuestionView } from '@/api/types';

export interface QuizTally {
  correct: number;
  incorrect: number;
  ungraded: number;
  graded: number;
  total: number;
  scorePercent: number | null;
}

export function tallyAttempt(attempt: QuizAttemptResponse): QuizTally {
  const total = Math.max(0, attempt.total_questions);
  const graded = Math.min(Math.max(0, attempt.graded_count), total);
  const correct = Math.min(Math.max(0, attempt.correct_count), graded);

  return {
    correct,
    incorrect: graded - correct,
    ungraded: total - graded,
    graded,
    total,
    scorePercent:
      graded === 0 || attempt.score === null ? null : Math.round(attempt.score * 100),
  };
}

export function describeSubmittedAnswer(
  question: QuizQuestionView,
  answer: QuizAnswerResult,
): string | null {
  if (answer.text_response) {
    return answer.text_response;
  }
  if (answer.selected_option_index !== null && question.options) {
    return question.options[answer.selected_option_index] ?? null;
  }
  return null;
}

export function describeCorrectAnswer(question: QuizQuestionView): string {
  const answer = question.correct_answer;
  if (!answer) {
    if (question.correct_option_index !== null && question.options) {
      return question.options[question.correct_option_index] ?? '';
    }
    return '';
  }
  switch (answer.type) {
    case 'multiple_choice':
      return question.options?.[answer.option_index] ?? '';
    case 'true_false':
      return answer.value ? 'True' : 'False';
    case 'short_answer':
      return answer.text;
    case 'open_ended':
      return answer.reference_answer;
  }
}

export function formatDuration(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds < 10 ? '0' : ''}${seconds}`;
}
