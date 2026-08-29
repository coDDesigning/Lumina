import { describe, expect, it } from 'vitest';
import type { ExamPlanTopicView, ExamPlanView } from '@/api/types';
import { comparePlans } from './comparePlans';

function topic(overrides: Partial<ExamPlanTopicView> = {}): ExamPlanTopicView {
  return {
    topic_key: 'hashing',
    display_label: 'Hashing',
    rank: 1,
    is_high_priority: false,
    priority_score: 70,
    priority_band: 'high',
    has_any_evidence: true,
    is_unattempted: false,
    mastery_percentage: 40,
    signals: {},
    reason_codes: [],
    explanation: '',
    ...overrides,
  };
}

function plan(overrides: Partial<ExamPlanView> = {}): ExamPlanView {
  return {
    generated_output_id: 1,
    analysis_output_id: 5,
    plan_version: 1,
    supersedes_output_id: null,
    created_at: '2026-05-01T10:00:00Z',
    exam_date: '2026-06-01',
    days_until_exam: 31,
    selection_mode: 'manual',
    manual_review_recommended: true,
    ranking_engine: 'deterministic',
    ranking_policy_version: 1,
    configured_weights: {},
    effective_weights: {},
    signals_available: { syllabus: true, past_exams: false },
    signal_bases: {},
    unmapped_mastery_labels: 0,
    warnings: [],
    topics: [topic()],
    staleness: { is_stale: false, requires_rescan: false, stale_reasons: [] },
    ...overrides,
  };
}

describe('comparePlans', () => {
  it('reports nothing changed between a plan and itself', () => {
    const result = comparePlans(plan(), plan());

    expect(result.added).toEqual([]);
    expect(result.removed).toEqual([]);
    expect(result.changed).toEqual([]);
    expect(result.unchanged).toHaveLength(1);
  });

  it('joins on the canonical key, so a relabelled topic is not a swap', () => {
    // The label is what a model wrote and can be reworded between analyses.
    // Matching on it would report one rename as a drop plus an addition.
    const before = plan({ topics: [topic({ display_label: 'Hash Tables' })] });
    const after = plan({ topics: [topic({ display_label: 'Hashing and Hash Tables' })] });

    const result = comparePlans(before, after);

    expect(result.added).toEqual([]);
    expect(result.removed).toEqual([]);
    expect(result.unchanged).toHaveLength(1);
    expect(result.unchanged[0].label).toBe('Hashing and Hash Tables');
  });

  it('names a topic the newer plan added', () => {
    const after = plan({
      topics: [topic(), topic({ topic_key: 'sorting', display_label: 'Sorting', rank: 2 })],
    });

    const result = comparePlans(plan(), after);

    expect(result.added.map((entry) => entry.topicKey)).toEqual(['sorting']);
    expect(result.added[0].before).toBeNull();
  });

  it('names a topic the newer plan dropped', () => {
    const before = plan({
      topics: [topic(), topic({ topic_key: 'sorting', display_label: 'Sorting', rank: 2 })],
    });

    const result = comparePlans(before, plan());

    expect(result.removed.map((entry) => entry.topicKey)).toEqual(['sorting']);
    expect(result.removed[0].after).toBeNull();
  });

  it('reports a rank move with its direction', () => {
    const before = plan({
      topics: [topic({ rank: 1 }), topic({ topic_key: 'sorting', display_label: 'Sorting', rank: 2 })],
    });
    const after = plan({
      topics: [
        topic({ topic_key: 'sorting', display_label: 'Sorting', rank: 1 }),
        topic({ rank: 2 }),
      ],
    });

    const result = comparePlans(before, after);

    const moved = Object.fromEntries(
      result.changed.map((entry) => [entry.topicKey, entry.rankDelta]),
    );
    expect(moved).toEqual({ hashing: 1, sorting: -1 });
  });

  it.each([
    [{ priority_band: 'critical' }, 'bandChanged'],
    [{ is_high_priority: true }, 'priorityChanged'],
    [{ mastery_percentage: 80 }, 'masteryChanged'],
  ] as const)('notices %o', (overrides, flag) => {
    const result = comparePlans(plan(), plan({ topics: [topic(overrides)] }));

    expect(result.changed).toHaveLength(1);
    expect(result.changed[0][flag]).toBe(true);
  });

  it('treats mastery arriving where there was none as a change', () => {
    // Null and a number are different facts, and "unattempted" becoming "40%"
    // is exactly the kind of movement a comparison exists to show.
    const before = plan({ topics: [topic({ mastery_percentage: null, is_unattempted: true })] });

    const result = comparePlans(before, plan());

    expect(result.changed[0].masteryChanged).toBe(true);
  });

  it('reports the analysis, the exam date, and signal availability moving', () => {
    const after = plan({
      analysis_output_id: 6,
      exam_date: '2026-06-15',
      signals_available: { syllabus: true, past_exams: true },
    });

    const result = comparePlans(plan(), after);

    expect(result.analysisChanged).toBe(true);
    expect(result.examDateChanged).toBe(true);
    expect(result.signalsChanged).toEqual(['past_exams']);
  });
});
