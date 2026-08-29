import { describe, expect, it } from 'vitest';
import type { ExamSourceDocument, ExamSourceInventory } from '@/api/types';
import { deriveReadiness, examDateHasPassed } from './examPrerequisites';

const TODAY = new Date('2026-05-01T09:00:00Z');

function document(overrides: Partial<ExamSourceDocument> = {}): ExamSourceDocument {
  return {
    id: 'aaaaaaaa-0000-0000-0000-000000000001',
    label: 'Lecture 4',
    material_kind: 'lecture',
    status: 'ready',
    is_past_exam: false,
    is_syllabus: false,
    ...overrides,
  };
}

function inventory(overrides: Partial<ExamSourceInventory> = {}): ExamSourceInventory {
  return {
    syllabus_present: true,
    syllabus_characters: 400,
    course_topics: ['Graph Traversal'],
    documents: [document()],
    ready_document_count: 1,
    past_exam_document_count: 0,
    chunks_available: 12,
    ...overrides,
  };
}

function kinds(entries: { kind: string }[]): string[] {
  return entries.map((entry) => entry.kind);
}

describe('examDateHasPassed', () => {
  it.each([
    ['2026-05-02', false],
    ['2026-05-01', false],
    ['2026-04-30', true],
    ['not-a-date', false],
  ])('reads %s as passed: %s', (examDate, expected) => {
    expect(examDateHasPassed(examDate, TODAY)).toBe(expected);
  });
});

describe('deriveReadiness', () => {
  it('clears a course with sources, a syllabus, topics and a future exam', () => {
    const readiness = deriveReadiness({
      inventory: inventory(),
      examDate: '2026-06-01',
      hasPlan: false,
      today: TODAY,
    });

    expect(readiness.blockers).toEqual([]);
    expect(readiness.warnings).toEqual([]);
    expect(readiness.canAnalyse).toBe(true);
    expect(readiness.canPlan).toBe(true);
  });

  it('blocks a first plan when the course has no exam date', () => {
    const readiness = deriveReadiness({
      inventory: inventory(),
      examDate: null,
      hasPlan: false,
      today: TODAY,
    });

    expect(kinds(readiness.blockers)).toEqual(['exam_date_missing']);
    expect(readiness.canPlan).toBe(false);
    // The date gates planning, never reading the sources it would plan from.
    expect(readiness.canAnalyse).toBe(true);
  });

  it('blocks a first plan when the exam date has already passed', () => {
    const readiness = deriveReadiness({
      inventory: inventory(),
      examDate: '2026-04-30',
      hasPlan: false,
      today: TODAY,
    });

    expect(kinds(readiness.blockers)).toEqual(['exam_date_passed']);
    expect(readiness.canPlan).toBe(false);
  });

  it('blocks nothing once a plan exists, however old the exam date is', () => {
    // A plan is a study resource. It does not expire with its exam.
    const readiness = deriveReadiness({
      inventory: inventory(),
      examDate: '2026-04-30',
      hasPlan: true,
      today: TODAY,
    });

    expect(readiness.blockers).toEqual([]);
    expect(kinds(readiness.warnings)).toEqual(['exam_date_passed']);
    expect(readiness.canPlan).toBe(true);
  });

  it('blocks analysis when no source is ready', () => {
    const readiness = deriveReadiness({
      inventory: inventory({
        documents: [document({ status: 'processing' })],
        ready_document_count: 0,
        chunks_available: 0,
      }),
      examDate: '2026-06-01',
      hasPlan: false,
      today: TODAY,
    });

    expect(kinds(readiness.blockers)).toEqual(['no_sources']);
    expect(readiness.canAnalyse).toBe(false);
  });

  it('names the sources still being read rather than calling the course empty', () => {
    const readiness = deriveReadiness({
      inventory: inventory({
        documents: [document(), document({ id: 'b', label: 'Lecture 5', status: 'processing' })],
      }),
      examDate: '2026-06-01',
      hasPlan: false,
      today: TODAY,
    });

    expect(readiness.blockers).toEqual([]);
    expect(kinds(readiness.warnings)).toEqual(['sources_processing']);
    expect(readiness.warnings[0].documents.map((entry) => entry.label)).toEqual(['Lecture 5']);
  });

  it('reports a failed source without counting it as usable material', () => {
    const readiness = deriveReadiness({
      inventory: inventory({
        documents: [document(), document({ id: 'b', label: 'Broken', status: 'failed' })],
      }),
      examDate: '2026-06-01',
      hasPlan: false,
      today: TODAY,
    });

    expect(kinds(readiness.warnings)).toEqual(['source_failed']);
    expect(readiness.readyDocuments.map((entry) => entry.label)).toEqual(['Lecture 4']);
  });

  it('separates an indexing gap from an empty course', () => {
    // Ready with nothing searchable is our problem, not a missing upload, and
    // the two must not collapse into one message.
    const readiness = deriveReadiness({
      inventory: inventory({ chunks_available: 0 }),
      examDate: '2026-06-01',
      hasPlan: false,
      today: TODAY,
    });

    expect(kinds(readiness.blockers)).toEqual(['material_not_indexed']);
    expect(readiness.canAnalyse).toBe(false);
  });

  it.each([
    [{ syllabus_present: false }, 'no_syllabus'],
    [{ course_topics: [] }, 'no_course_topics'],
  ])('treats %o as a warning, not a blocker', (overrides, expected) => {
    // The backend can discover topics from selected material alone, so
    // refusing here would claim a requirement it does not have.
    const readiness = deriveReadiness({
      inventory: inventory(overrides),
      examDate: '2026-06-01',
      hasPlan: false,
      today: TODAY,
    });

    expect(readiness.blockers).toEqual([]);
    expect(kinds(readiness.warnings)).toContain(expected);
    expect(readiness.canAnalyse).toBe(true);
  });
});
