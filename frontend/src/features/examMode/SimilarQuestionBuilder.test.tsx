import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { examModeAPI } from '@/api/examMode';
import type { ExamQuestionPage, ExamQuestionView } from '@/api/types';
import { queryCache } from '@/lib/query/cache';
import { SimilarQuestionBuilder } from './SimilarQuestionBuilder';

vi.mock('@/api/examMode', () => ({
  examModeAPI: {
    listQuestions: vi.fn(),
    generateSimilarQuestions: vi.fn(),
  },
}));

const credits = {
  status: null,
  isLoading: false,
  error: null,
  refresh: vi.fn(),
  isMetered: false,
  costOf: () => null,
  canAfford: () => true,
};

vi.mock('@/context/CreditContext', () => ({ useCredits: () => credits }));

const PAPER_2024 = 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa';
const PAPER_2023 = 'bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb';

function question(overrides: Partial<ExamQuestionView> = {}): ExamQuestionView {
  return {
    position: 0,
    document_id: PAPER_2024,
    question_text: 'Explain breadth-first search.',
    subparts: [],
    question_type: 'structured',
    marking_points: [],
    visual_refs: [],
    topic_mappings: [],
    citations: [],
    ...overrides,
  } as ExamQuestionView;
}

/** Two papers whose first questions collide on position, which is per-paper. */
function page(): ExamQuestionPage {
  return {
    analysis_output_id: 5,
    document_ids: [PAPER_2024, PAPER_2023],
    total: 2,
    limit: 50,
    offset: 0,
    questions: [
      question(),
      question({
        position: 0,
        document_id: PAPER_2023,
        question_text: 'Prove BFS visits every reachable vertex once.',
      }),
    ],
  };
}

function renderBuilder() {
  render(
    <MemoryRouter>
      <SimilarQuestionBuilder courseId={1} planId={7} analysisId={5} topicKey="graph-traversal" />
    </MemoryRouter>,
  );
}

describe('SimilarQuestionBuilder', () => {
  beforeEach(() => {
    queryCache.clear();
    vi.clearAllMocks();
    vi.mocked(examModeAPI.listQuestions).mockResolvedValue(page());
  });

  it('names a chosen question by the paper it was printed in and its position', async () => {
    const generate = vi.mocked(examModeAPI.generateSimilarQuestions);
    generate.mockRejectedValue(new Error('stop before navigating'));
    const user = userEvent.setup();

    renderBuilder();
    await waitFor(() => expect(screen.getAllByRole('checkbox')).toHaveLength(2));

    await user.click(screen.getAllByRole('checkbox')[1]);
    await user.click(screen.getByRole('button', { name: /write similar questions/i }));

    await waitFor(() => expect(generate).toHaveBeenCalled());
    expect(generate.mock.calls[0][2]).toMatchObject({
      plan_output_id: 7,
      source_questions: [{ document_id: PAPER_2023, position: 0 }],
    });
  });

  it('refuses a count the server would reject, before spending a request', async () => {
    // The server answers an out-of-range count with a raw validation string.
    // Blocking it here is what keeps that string off the student's screen.
    const generate = vi.mocked(examModeAPI.generateSimilarQuestions);
    const user = userEvent.setup();

    renderBuilder();
    await waitFor(() => expect(screen.getAllByRole('checkbox')).toHaveLength(2));
    await user.click(screen.getAllByRole('checkbox')[1]);

    await user.clear(screen.getByLabelText(/how many to write/i));

    expect(screen.getByText('Enter how many questions you want.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /write similar questions/i })).toBeDisabled();
    expect(generate).not.toHaveBeenCalled();
  });

  it('refuses a count above the bound the backend enforces', async () => {
    const generate = vi.mocked(examModeAPI.generateSimilarQuestions);
    const user = userEvent.setup();

    renderBuilder();
    await waitFor(() => expect(screen.getAllByRole('checkbox')).toHaveLength(2));
    await user.click(screen.getAllByRole('checkbox')[1]);

    const field = screen.getByLabelText(/how many to write/i);
    await user.clear(field);
    await user.type(field, '21');

    expect(screen.getByText('Choose between 1 and 20 questions.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /write similar questions/i })).toBeDisabled();
    expect(generate).not.toHaveBeenCalled();
  });

  it('ticks one paper without ticking the same position in the other', async () => {
    const user = userEvent.setup();

    renderBuilder();
    await waitFor(() => expect(screen.getAllByRole('checkbox')).toHaveLength(2));

    await user.click(screen.getAllByRole('checkbox')[0]);

    const [first, second] = screen.getAllByRole('checkbox') as HTMLInputElement[];
    expect(first.checked).toBe(true);
    expect(second.checked).toBe(false);
  });
});
