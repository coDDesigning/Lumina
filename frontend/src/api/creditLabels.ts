import type { AdminCreditReason, CreditTransaction } from './types';

const REASON_LABELS: Record<string, string> = {
  initial_grant: 'Welcome credits',
  periodic_grant: 'Monthly credits',
  generation_charge: 'AI generation',
  generation_refund: 'Refund',
  admin_grant: 'Administrator grant',
  support_compensation: 'Support compensation',
  admin_adjustment: 'Administrator adjustment',
  metering_reset: 'Balance re-baselined',
  migration_reconciliation: 'Opening balance',
};

const SOURCE_LABELS: Record<string, string> = {
  study_guide: 'Study guide',
  quiz: 'Quiz',
  quiz_grading: 'Quiz grading',
  flashcard: 'Flashcards',
  ai_tutor: 'AI tutor',
  course_qa: 'Course Q&A',
  prompt_generator: 'Prompt generator',
  exam_topic_analysis: 'Exam source analysis',
  exam_topic_analysis_rescan: 'Exam source rescan',
  exam_topic_unlock: 'Exam topic',
  exam_mock_exam: 'Mock exam',
  exam_review_sheet: 'Review sheet',
};

export const GENERATION_COST_LABELS: Record<string, string> = {
  study_guide: 'Study guide',
  quiz: 'Quiz',
  quiz_open_ended: 'Quiz including written questions',
  flashcard: 'Flashcards',
  course_qa: 'A question',
  ai_tutor: 'A tutoring turn',
  prompt_generator: 'A written prompt',
  exam_topic_analysis: 'Exam source analysis',
  exam_topic_analysis_rescan: 'Exam source rescan',
  exam_topic_unlock: 'Exam topic',
  exam_mock_exam: 'Mock exam',
  exam_review_sheet: 'Review sheet',
};

export const CANONICAL_COST_ORDER: readonly string[] = [
  'study_guide',
  'quiz',
  'quiz_open_ended',
  'flashcard',
  'course_qa',
  'ai_tutor',
  'prompt_generator',
  'exam_topic_analysis',
  'exam_topic_analysis_rescan',
  'exam_topic_unlock',
  'exam_mock_exam',
  'exam_review_sheet',
];

export function generationCostLabel(source: string): string {
  return (
    GENERATION_COST_LABELS[source] ??
    source
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (char) => char.toUpperCase())
  );
}

export const ADMIN_CREDIT_REASONS: readonly AdminCreditReason[] = [
  'admin_grant',
  'support_compensation',
  'admin_adjustment',
];

export const POSITIVE_ONLY_ADMIN_REASONS: ReadonlySet<AdminCreditReason> =
  new Set<AdminCreditReason>(['admin_grant', 'support_compensation']);

export function reasonLabel(reason: string): string {
  return REASON_LABELS[reason] ?? reason.replace(/_/g, ' ');
}

/** What the transaction was for, preferring the specific feature over the category. */
export function transactionLabel(transaction: CreditTransaction): string {
  const base = reasonLabel(transaction.reason);
  if (transaction.source_type && SOURCE_LABELS[transaction.source_type]) {
    return `${base} — ${SOURCE_LABELS[transaction.source_type]}`;
  }
  return base;
}

export function formatDelta(delta: number): string {
  return delta > 0 ? `+${delta}` : `${delta}`;
}
