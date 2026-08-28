import { describe, expect, it } from 'vitest';
import type { ActivityItem } from '@/api/types';
import { activityHref } from './activityHref';

function item(overrides: Partial<ActivityItem>): ActivityItem {
  return {
    kind: 'generation',
    action_type: 'study_guide',
    course_id: 7,
    course_title: 'Computer Architecture',
    occurred_at: '2026-05-01T12:00:00Z',
    output_id: null,
    quiz_id: null,
    attempt_id: null,
    topic: null,
    score: null,
    ...overrides,
  };
}

describe('activityHref', () => {
  it('opens the attempt review for an attempt', () => {
    expect(
      activityHref(
        item({ kind: 'attempt', action_type: 'quiz_attempt', quiz_id: 3, attempt_id: 9 }),
      ),
    ).toBe('/courses/7/practice/3/attempts/9');
  });

  it('opens the stored guide for a study guide', () => {
    expect(activityHref(item({ action_type: 'study_guide', output_id: 12 }))).toBe(
      '/courses/7?artifact=12',
    );
  });

  it('opens the quiz for a generated quiz', () => {
    expect(activityHref(item({ action_type: 'quiz', output_id: 12, quiz_id: 4 }))).toBe(
      '/courses/7/practice/4',
    );
  });

  it('falls back to the course when a generated quiz has no quiz to open', () => {
    expect(activityHref(item({ action_type: 'quiz', output_id: 12 }))).toBe('/courses/7');
  });

  it('falls back to the course for flashcards, which have no page of their own', () => {
    expect(activityHref(item({ action_type: 'flashcards', output_id: 12 }))).toBe(
      '/courses/7',
    );
  });

  it('falls back to the course for an output type it does not know', () => {
    expect(activityHref(item({ action_type: 'summary', output_id: 12 }))).toBe(
      '/courses/7',
    );
  });

  it('falls back to the course when an attempt is missing its identifiers', () => {
    expect(
      activityHref(item({ kind: 'attempt', action_type: 'quiz_attempt', quiz_id: 3 })),
    ).toBe('/courses/7');
  });
});
