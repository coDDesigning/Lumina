import type { CreditTransaction } from './types';

const REASON_LABELS: Record<string, string> = {
  initial_grant: 'Welcome credits',
  periodic_grant: 'Monthly credits',
  generation_charge: 'AI generation',
  generation_refund: 'Refund',
  admin_grant: 'Administrator grant',
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
};

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
