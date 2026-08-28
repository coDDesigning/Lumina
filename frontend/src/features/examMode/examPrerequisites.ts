import type { ExamSourceDocument, ExamSourceInventory } from '@/api/types';

/**
 * What stands between a course and an exam plan.
 *
 * Pure, so every state a student can land in is a table rather than a branch
 * discovered in production. Two rules shape it:
 *
 * A blocker is something the student must fix before the operation can run at
 * all. A warning is something that makes the result worse but not impossible --
 * a course with no syllabus can still have its topics discovered from selected
 * material, so refusing there would be a claim the backend does not make.
 *
 * The exam date gates only the *first* plan. Once a plan exists it stays
 * readable forever, because a plan is a study resource and does not expire with
 * the exam it was built for.
 */

export type PrerequisiteKind =
  | 'exam_date_missing'
  | 'exam_date_passed'
  | 'no_sources'
  | 'sources_processing'
  | 'source_failed'
  | 'material_not_indexed'
  | 'no_syllabus'
  | 'no_course_topics';

export interface Prerequisite {
  kind: PrerequisiteKind;
  /** Documents this prerequisite is about, when it is about particular ones. */
  documents: ExamSourceDocument[];
}

export interface ExamReadiness {
  blockers: Prerequisite[];
  warnings: Prerequisite[];
  /** Sources can be analysed: something is ready and something is searchable. */
  canAnalyse: boolean;
  /** A first plan is allowed: the exam is still to come, or one already exists. */
  canPlan: boolean;
  readyDocuments: ExamSourceDocument[];
}

const READY = 'ready';
const FAILED = 'failed';
const IN_FLIGHT = new Set(['uploaded', 'processing']);

export function isReady(document: ExamSourceDocument): boolean {
  return document.status === READY;
}

/**
 * `today` is supplied rather than read, so a date boundary is a table row.
 * Compared as calendar dates: an exam happening today has not passed.
 */
export function examDateHasPassed(examDate: string, today: Date): boolean {
  const exam = Date.parse(`${examDate}T00:00:00Z`);
  if (Number.isNaN(exam)) return false;
  const midnight = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  return exam < midnight;
}

export interface ReadinessInput {
  inventory: ExamSourceInventory;
  examDate: string | null;
  hasPlan: boolean;
  today: Date;
}

export function deriveReadiness({
  inventory,
  examDate,
  hasPlan,
  today,
}: ReadinessInput): ExamReadiness {
  const blockers: Prerequisite[] = [];
  const warnings: Prerequisite[] = [];

  const readyDocuments = inventory.documents.filter(isReady);
  const processing = inventory.documents.filter((document) => IN_FLIGHT.has(document.status));
  const failed = inventory.documents.filter((document) => document.status === FAILED);

  if (readyDocuments.length === 0) {
    blockers.push({ kind: 'no_sources', documents: [] });
  } else if (inventory.chunks_available === 0) {
    // Ready but unsearchable is an indexing gap on our side. Telling the
    // student to upload the same file again would be the wrong instruction.
    blockers.push({ kind: 'material_not_indexed', documents: [] });
  }

  if (processing.length > 0) {
    warnings.push({ kind: 'sources_processing', documents: processing });
  }
  if (failed.length > 0) {
    warnings.push({ kind: 'source_failed', documents: failed });
  }
  if (!inventory.syllabus_present) {
    warnings.push({ kind: 'no_syllabus', documents: [] });
  }
  if (inventory.course_topics.length === 0) {
    warnings.push({ kind: 'no_course_topics', documents: [] });
  }

  const canAnalyse = readyDocuments.length > 0 && inventory.chunks_available > 0;

  let canPlan = true;
  if (!hasPlan) {
    if (!examDate) {
      blockers.push({ kind: 'exam_date_missing', documents: [] });
      canPlan = false;
    } else if (examDateHasPassed(examDate, today)) {
      blockers.push({ kind: 'exam_date_passed', documents: [] });
      canPlan = false;
    }
  } else if (examDate && examDateHasPassed(examDate, today)) {
    // Nothing is blocked. Saved work stays readable; only a new plan would
    // need a new date, and the student is told that rather than refused.
    warnings.push({ kind: 'exam_date_passed', documents: [] });
  }

  return { blockers, warnings, canAnalyse, canPlan, readyDocuments };
}
