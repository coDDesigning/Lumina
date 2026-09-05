import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ExamMockExamBuilder } from './ExamMockExamBuilder';
import { examModeAPI } from '@/api/examMode';
import type { ExamPlanView } from '@/api/types';
import { queryCache } from '@/lib/query/cache';

vi.mock('@/api/examMode', () => ({
  examModeAPI: {
    generateMockExam: vi.fn(),
  },
}));

const credits = { isMetered: false, canAfford: () => true };

vi.mock('@/context/CreditContext', () => ({
  useCredits: () => credits,
}));

function topic(overrides: Partial<ExamPlanView['topics'][number]> = {}) {
  return {
    topic_key: 'photosynthesis',
    display_label: 'Photosynthesis',
    rank: 1,
    is_high_priority: true,
    priority_score: 82,
    priority_band: 'high',
    has_any_evidence: true,
    is_unattempted: false,
    mastery_percentage: 40,
    signals: {},
    reason_codes: [],
    explanation: 'Weighted highly by syllabus emphasis.',
    ...overrides,
  };
}

function planFixture(overrides: Partial<ExamPlanView> = {}): ExamPlanView {
  return {
    generated_output_id: 7,
    analysis_output_id: 5,
    plan_version: 1,
    supersedes_output_id: null,
    created_at: '2026-08-20T10:00:00Z',
    exam_date: '2026-12-01',
    days_until_exam: 30,
    selection_mode: 'manual',
    manual_review_recommended: false,
    ranking_engine: 'python',
    ranking_policy_version: 1,
    configured_weights: {},
    effective_weights: {},
    signals_available: {},
    signal_bases: {},
    unmapped_mastery_labels: 0,
    warnings: [],
    topics: [topic()],
    staleness: { is_stale: false, requires_rescan: false, stale_reasons: [] },
    ...overrides,
  } as ExamPlanView;
}

function renderBuilder(plan: ExamPlanView = planFixture()) {
  render(
    <MemoryRouter>
      <ExamMockExamBuilder courseId={1} plan={plan} />
    </MemoryRouter>,
  );
}

async function setQuestions(user: ReturnType<typeof userEvent.setup>, value: string) {
  const field = screen.getByLabelText(/^questions$/i);
  await user.clear(field);
  if (value !== '') await user.type(field, value);
}

describe('ExamMockExamBuilder', () => {
  beforeEach(() => {
    queryCache.clear();
    vi.clearAllMocks();
  });

  it('refuses a paper too short to carry every question type it was given', async () => {
    // A type that rounds to zero is dropped from the mix silently, so the
    // student would sit a paper missing a format they explicitly asked for.
    const generate = vi.mocked(examModeAPI.generateMockExam);
    const user = userEvent.setup();

    renderBuilder();
    await user.click(screen.getByRole('checkbox', { name: /true or false/i }));
    await user.click(screen.getByRole('checkbox', { name: /short answer/i }));
    await setQuestions(user, '2');

    expect(
      screen.getByText(
        'Every type gets at least one question, so 3 question types need at least 3 questions.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /write the paper/i })).toBeDisabled();
    expect(generate).not.toHaveBeenCalled();
  });

  it('builds the paper once the count covers every chosen type', async () => {
    const generate = vi.mocked(examModeAPI.generateMockExam);
    generate.mockRejectedValue(new Error('stop before navigating'));
    const user = userEvent.setup();

    renderBuilder();
    await user.click(screen.getByRole('checkbox', { name: /true or false/i }));
    await user.click(screen.getByRole('checkbox', { name: /short answer/i }));
    await setQuestions(user, '3');

    expect(screen.queryByText(/this paper cannot be built yet/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /write the paper/i }));

    await waitFor(() => expect(generate).toHaveBeenCalled());
    expect(generate.mock.calls[0][1]).toMatchObject({
      question_count: 3,
      question_mix: [
        { question_type: 'multiple_choice', count: 1 },
        { question_type: 'true_false', count: 1 },
        { question_type: 'short_answer', count: 1 },
      ],
    });
  });

  it('refuses a cleared count rather than sending one the server cannot read', async () => {
    const generate = vi.mocked(examModeAPI.generateMockExam);
    const user = userEvent.setup();

    renderBuilder();
    await setQuestions(user, '');

    expect(screen.getByRole('button', { name: /write the paper/i })).toBeDisabled();
    expect(generate).not.toHaveBeenCalled();
  });

  it('refuses a count above the bound the backend enforces', async () => {
    const generate = vi.mocked(examModeAPI.generateMockExam);
    const user = userEvent.setup();

    renderBuilder();
    await setQuestions(user, '21');

    expect(screen.getByText('Choose between 1 and 20 questions.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /write the paper/i })).toBeDisabled();
    expect(generate).not.toHaveBeenCalled();
  });
});
