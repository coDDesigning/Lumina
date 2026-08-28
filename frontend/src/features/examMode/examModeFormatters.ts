import type { ExamPlanStaleness } from '@/api/types';

const DATE = new Intl.DateTimeFormat('en', { day: 'numeric', month: 'short', year: 'numeric' });
const DATE_TIME = new Intl.DateTimeFormat('en', {
  day: 'numeric',
  month: 'short',
  hour: 'numeric',
  minute: '2-digit',
});

export function formatExamDate(iso: string): string {
  const parsed = Date.parse(`${iso}T00:00:00Z`);
  return Number.isNaN(parsed) ? iso : DATE.format(parsed);
}

export function formatCreatedAt(iso: string): string {
  const parsed = Date.parse(iso);
  return Number.isNaN(parsed) ? iso : DATE_TIME.format(parsed);
}

/**
 * A roadmap day is shown as "Day 1", not as a calendar date.
 *
 * The schedule's length comes from a real horizon, but a student who falls a
 * day behind has not invalidated their plan -- naming the date would tell them
 * they had.
 */
export function formatRoadmapDay(dayIndex: number): string {
  return `Day ${dayIndex}`;
}

/** Plain words for the reasons a stored plan no longer matches the course. */
const STALE_REASONS: Record<string, string> = {
  exam_date_changed: 'the exam date moved',
  syllabus_changed: 'the syllabus changed',
  course_topics_changed: 'the course topics changed',
  documents_added: 'sources were added',
  documents_removed: 'sources were removed',
  documents_changed: 'sources changed',
  documents_reprocessed: 'sources were processed again',
  past_exams_changed: 'past papers changed',
  new_quiz_results: 'you have new quiz results',
  mastery_changed: 'your mastery changed',
  selection_changed: 'the topic selection changed',
  ranking_policy_updated: 'the ranking policy was updated',
  topic_keys_updated: 'topic identity was updated',
};

export function describeStaleReasons(reasons: string[]): string {
  const words = reasons.map((reason) => STALE_REASONS[reason] ?? reason.replace(/_/g, ' '));
  if (words.length === 0) return '';
  if (words.length === 1) return words[0];
  return `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`;
}

export interface StalenessAction {
  label: string;
  /** What the student is actually buying, so the copy can be honest about it. */
  detail: string;
}

/**
 * Two different remedies, never one button called "Regenerate".
 *
 * A moved exam date only reorders what has already been read, and costs
 * nothing. Anything else means the sources have to be read again, which is a
 * separate operation with its own price.
 */
export function stalenessAction(staleness: ExamPlanStaleness): StalenessAction | null {
  if (!staleness.is_stale && !staleness.requires_rescan) return null;
  if (staleness.requires_rescan) {
    return {
      label: 'Scan sources again',
      detail:
        'Reads your selected sources again and lets you review what carried over before creating a new version.',
    };
  }
  return {
    label: 'Refresh ranking',
    detail:
      'Ranks the same topics against what has changed and saves the result as a new version. Nothing is read again and nothing is charged.',
  };
}
/**
 * What a plan could not see, in the student's words.
 *
 * These are facts about the evidence the ranking had, not failures. A missing
 * signal is removed and its weight redistributed rather than scored as zero,
 * so the honest thing to say is which signal was absent -- never the code that
 * names it internally.
 */
const PLAN_WARNINGS: Record<string, string> = {
  no_syllabus_evidence:
    'No syllabus evidence was available, so syllabus emphasis counted for nothing and its weight went to the signals that were.',
  no_past_exam_evidence:
    'No past-paper questions were available, so how often a topic has been examined could not be counted.',
  no_mastery_evidence:
    'You have no quiz results in this course yet, so how well you know each topic could not be weighed.',
  sparse_material_coverage:
    'Some topics have little or no material behind them, so their coverage is thinner than the rest.',
  unmapped_mastery_labels:
    'Some of your quiz results are tagged with topics this plan does not rank, so they did not count toward any of them.',
};

export function describePlanWarning(code: string): string {
  return PLAN_WARNINGS[code] ?? code.replace(/_/g, ' ');
}

/** True when nothing more useful than the raw code could be said. */
export function isKnownPlanWarning(code: string): boolean {
  return code in PLAN_WARNINGS;
}
