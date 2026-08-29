import { MalformedResponseError } from './client';
import type {
  Citation,
  Coverage,
  ExamTopicGuideDocument,
  ExamTopicPitfall,
  ExamTopicSection,
  ExamTopicTerm,
  MaybeCited,
} from './types';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function isCitation(value: unknown): value is Citation {
  if (!isRecord(value)) return false;
  return (
    typeof value.key === 'string' &&
    typeof value.document_id === 'string' &&
    typeof value.document_label === 'string' &&
    (value.page_start === null || isNumber(value.page_start)) &&
    (value.page_end === null || isNumber(value.page_end)) &&
    (value.version === undefined || isNumber(value.version))
  );
}

function isCitationList(value: unknown): value is Citation[] {
  return Array.isArray(value) && value.every(isCitation);
}

function isMaybeCited(value: unknown): value is MaybeCited {
  return (
    typeof value === 'string' ||
    (isRecord(value) && typeof value.text === 'string' && isCitationList(value.citations))
  );
}

function isCoverage(value: unknown): value is Coverage {
  return (
    isRecord(value) &&
    typeof value.status === 'string' &&
    isNumber(value.estimated_completeness)
  );
}

function isSection(value: unknown): value is ExamTopicSection {
  return (
    isRecord(value) &&
    typeof value.heading === 'string' &&
    isMaybeCited(value.body) &&
    Array.isArray(value.key_points) &&
    value.key_points.every(isMaybeCited)
  );
}

function isTerm(value: unknown): value is ExamTopicTerm {
  return (
    isRecord(value) &&
    typeof value.term === 'string' &&
    typeof value.definition === 'string' &&
    isCitationList(value.citations)
  );
}

function isPitfall(value: unknown): value is ExamTopicPitfall {
  return (
    isRecord(value) &&
    typeof value.mistake === 'string' &&
    typeof value.correction === 'string' &&
    isCitationList(value.citations)
  );
}

export function isExamTopicGuideDocument(value: unknown): value is ExamTopicGuideDocument {
  if (!isRecord(value)) return false;
  return (
    value.version === 1 &&
    value.output_type === 'exam_topic_guide' &&
    typeof value.topic_key === 'string' &&
    typeof value.display_label === 'string' &&
    isNumber(value.plan_output_id) &&
    isNumber(value.rank) &&
    typeof value.priority_band === 'string' &&
    typeof value.title === 'string' &&
    isMaybeCited(value.overview) &&
    Array.isArray(value.sections) &&
    value.sections.every(isSection) &&
    Array.isArray(value.key_terms) &&
    value.key_terms.every(isTerm) &&
    Array.isArray(value.common_pitfalls) &&
    value.common_pitfalls.every(isPitfall) &&
    Array.isArray(value.what_to_be_able_to_do) &&
    value.what_to_be_able_to_do.every(isMaybeCited) &&
    (value.coverage === undefined || value.coverage === null || isCoverage(value.coverage)) &&
    typeof value.confidence_notes === 'string'
  );
}

export function parseExamTopicGuideDocument(value: unknown): ExamTopicGuideDocument {
  if (!isExamTopicGuideDocument(value)) {
    throw new MalformedResponseError('Exam topic guide', 'invalid_data');
  }
  return value;
}
