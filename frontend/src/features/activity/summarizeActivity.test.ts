import { describe, expect, it } from 'vitest';
import type { ActivityItem } from '@/api/types';
import { RHYTHM_DAYS, summarizeActivity } from './summarizeActivity';

const NOW = new Date(2026, 8, 19, 15, 0);

function daysAgo(days: number): string {
  const date = new Date(NOW);
  date.setDate(date.getDate() - days);
  return date.toISOString();
}

function item(overrides: Partial<ActivityItem>): ActivityItem {
  return {
    kind: 'generation',
    action_type: 'study_guide',
    course_id: 1,
    course_title: 'Algorithms',
    occurred_at: daysAgo(0),
    output_id: 1,
    quiz_id: null,
    attempt_id: null,
    topic: null,
    score: null,
    ...overrides,
  };
}

describe('summarizeActivity', () => {
  it('counts each day of the last four weeks, oldest first', () => {
    const summary = summarizeActivity(
      [item({}), item({}), item({ occurred_at: daysAgo(3) }), item({ occurred_at: daysAgo(40) })],
      NOW,
    );

    expect(summary.rhythm).toHaveLength(RHYTHM_DAYS);
    expect(summary.rhythm[RHYTHM_DAYS - 1].count).toBe(2);
    expect(summary.rhythm[RHYTHM_DAYS - 4].count).toBe(1);
    expect(summary.activeDays).toBe(2);
  });

  it('compares the last seven days with the seven before', () => {
    const summary = summarizeActivity(
      [item({}), item({ occurred_at: daysAgo(6) }), item({ occurred_at: daysAgo(9) })],
      NOW,
    );

    expect(summary.thisWeek).toBe(2);
    expect(summary.lastWeek).toBe(1);
  });

  it('averages only scored quiz attempts', () => {
    const summary = summarizeActivity(
      [
        item({ kind: 'attempt', action_type: 'quiz_attempt', score: 0.5 }),
        item({ kind: 'attempt', action_type: 'quiz_attempt', score: 0.7 }),
        item({ kind: 'attempt', action_type: 'quiz_attempt', score: null }),
      ],
      NOW,
    );

    expect(summary.attemptCount).toBe(3);
    expect(summary.averageScore).toBeCloseTo(0.6);
  });

  it('has no average when no attempt was scored', () => {
    expect(summarizeActivity([item({})], NOW).averageScore).toBeNull();
  });

  it('ranks courses by activity and folds the long tail together', () => {
    const items = [
      item({ course_id: 1, course_title: 'A' }),
      item({ course_id: 2, course_title: 'B' }),
      item({ course_id: 2, course_title: 'B' }),
      item({ course_id: 3, course_title: 'C' }),
      item({ course_id: 4, course_title: 'D' }),
      item({ course_id: 5, course_title: 'E' }),
      item({ course_id: 6, course_title: 'F' }),
    ];

    const { courses } = summarizeActivity(items, NOW);

    expect(courses[0]).toMatchObject({ courseId: 2, count: 2 });
    expect(courses).toHaveLength(5);
    expect(courses[4]).toMatchObject({ courseId: null, title: 'Other courses', count: 2 });
  });
});
