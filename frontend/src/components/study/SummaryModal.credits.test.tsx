import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SummaryModal } from './SummaryModal';
import { CreditProvider } from '../../context/CreditContext';
import { studyGuideAPI } from '../../api/studyGuide';
import { userAPI } from '../../api/user';
import { APIError } from '../../api/client';
import type { CreditStatus, StudyGuideGenerationResult } from '../../api/types';

vi.mock('../../api/studyGuide', () => ({
  studyGuideAPI: { generate: vi.fn() },
}));

vi.mock('../../api/user', () => ({
  userAPI: { getCredits: vi.fn() },
}));

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: true }),
}));

const mockGenerate = vi.mocked(studyGuideAPI.generate);
const mockGetCredits = vi.mocked(userAPI.getCredits);

const GUIDE: StudyGuideGenerationResult = {
  generated_output_id: 12,
  context_truncated: false,
  retrieval_narrowed: false,
  lowest_similarity: 0.41,
  highest_similarity: 0.88,
  chunks_used: 4,
  chunks_available: 10,
  study_guide: {
    title: 'Sorting Algorithms',
    summary: 'Example summary',
    key_points: ['Point one'],
    important_terms: [{ term: 'Term', definition: 'Definition' }],
    common_mistakes: [{ mistake: 'Mistake', correction: 'Correction' }],
    exam_tips: { lecture_based: ['Tip'], ai_suggestions: ['Suggestion'] },
    difficulty: { level: 'Medium', reason: 'Mixed material' },
    estimated_study_time: '45 minutes',
    prerequisites: ['Algebra'],
    learning_objectives: ['Understand the basics'],
    coverage: { status: 'Partial', estimated_completeness: 40 },
    confidence_notes: '',
  },
};

function status(credits: number | null): CreditStatus {
  return {
    credits,
    metering_enabled: true,
    monthly_grant: 50,
    balance_cap: 100,
    next_grant_at: '2026-09-01T00:00:00Z',
    generation_costs: { study_guide: 1 },
  };
}

function renderModal() {
  return render(
    <CreditProvider>
      <SummaryModal
        courseId={1}
        courseName="Algorithms"
        topics={['All Topics']}
        readyDocumentCount={3}
        onClose={vi.fn()}
      />
    </CreditProvider>,
  );
}

beforeEach(() => {
  mockGenerate.mockReset();
  mockGetCredits.mockReset();
});

describe('SummaryModal credit handling', () => {
  it('shows the remaining balance beside the generate action', async () => {
    mockGetCredits.mockResolvedValue(status(12));
    renderModal();

    await waitFor(() => expect(screen.getByText('12')).toBeInTheDocument());
    expect(
      screen.getByRole('button', { name: /generate study guide/i }),
    ).toBeEnabled();
  });

  it('blocks generation and explains why when the balance cannot cover it', async () => {
    mockGetCredits.mockResolvedValue(status(0));
    renderModal();

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /generate study guide/i }),
      ).toBeDisabled(),
    );
    const notice = screen.getByRole('alert');
    expect(notice.textContent).toMatch(/don't have enough credits/i);
    expect(notice.textContent).toMatch(/credits refresh on/i);
    expect(notice.textContent).toMatch(/contact an administrator/i);
    expect(mockGenerate).not.toHaveBeenCalled();
  });

  it('keeps the generated guide on screen when a stale balance hits a 402', async () => {
    mockGetCredits.mockResolvedValueOnce(status(1));
    mockGenerate.mockResolvedValueOnce(GUIDE);
    renderModal();

    await waitFor(() => expect(screen.getByText('1')).toBeInTheDocument());
    await userEvent.click(
      screen.getByRole('button', { name: /generate study guide/i }),
    );
    await waitFor(() =>
      expect(screen.getByText('Sorting Algorithms')).toBeInTheDocument(),
    );

    // The balance is now stale: another tab spent it.
    mockGetCredits.mockResolvedValue(status(0));
    mockGenerate.mockRejectedValueOnce(
      new APIError(402, { detail: 'Not enough credits.' }, 'insufficient_credits'),
    );

    await userEvent.click(
      screen.getByRole('button', { name: /new study guide/i }),
    );
    await userEvent.click(
      screen.getByRole('button', { name: /generate study guide/i }),
    );

    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toMatch(
        /don't have enough credits/i,
      ),
    );
    // The guide the student was reading survives the refusal.
    expect(screen.getByText('Sorting Algorithms')).toBeInTheDocument();
    // And the screen cannot claim credits remain while saying there are none.
    expect(screen.queryByText('1')).toBeNull();
  });

  it('refreshes the balance after a successful generation rather than guessing', async () => {
    mockGetCredits.mockResolvedValueOnce(status(12));
    mockGenerate.mockResolvedValueOnce(GUIDE);
    renderModal();

    await waitFor(() => expect(screen.getByText('12')).toBeInTheDocument());
    mockGetCredits.mockResolvedValue(status(11));

    await userEvent.click(
      screen.getByRole('button', { name: /generate study guide/i }),
    );

    await waitFor(() => expect(mockGetCredits).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByText('11')).toBeInTheDocument());
  });

  it('reports a refunded failure at the original balance, never decremented locally', async () => {
    mockGetCredits.mockResolvedValue(status(20));
    mockGenerate.mockRejectedValueOnce(
      new APIError(503, { detail: 'The AI service is currently unavailable.' }),
    );
    renderModal();

    await waitFor(() => expect(screen.getByText('20')).toBeInTheDocument());
    await userEvent.click(
      screen.getByRole('button', { name: /generate study guide/i }),
    );

    await waitFor(() =>
      expect(screen.getByText(/AI service is currently unavailable/i)).toBeInTheDocument(),
    );
    expect(screen.getByText('20')).toBeInTheDocument();
  });

  it('shows no credit interface at all for an unmetered account', async () => {
    mockGetCredits.mockResolvedValue(status(null));
    renderModal();

    await waitFor(() => expect(mockGetCredits).toHaveBeenCalled());
    expect(screen.queryByText(/credits/i)).toBeNull();
    expect(
      screen.getByRole('button', { name: /generate study guide/i }),
    ).toBeEnabled();
  });
});
