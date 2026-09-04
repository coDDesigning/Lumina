import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { quizAPI } from '@/api/quiz';
import { userAPI } from '@/api/user';
import type { CreditStatus } from '@/api/types';
import { CreditProvider } from '@/context/CreditContext';
import { QuizModal } from './QuizModal';

vi.mock('@/api/quiz', () => ({
  quizAPI: { enqueue: vi.fn() },
}));

vi.mock('@/api/settings', () => ({
  settingsAPI: { get: vi.fn().mockResolvedValue({ difficulty: 'medium', question_count: 5 }) },
}));

vi.mock('@/api/user', () => ({
  userAPI: { getCredits: vi.fn() },
}));

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: true, user: { id: 1 } }),
}));

const mockEnqueue = vi.mocked(quizAPI.enqueue);
const mockGetCredits = vi.mocked(userAPI.getCredits);

const UNMETERED: CreditStatus = {
  credits: null,
  metering_enabled: false,
  email_verification_required: false,
  is_email_verified: true,
  monthly_grant: null,
  balance_cap: null,
  next_grant_at: null,
  generation_costs: {},
};

function generationError(code: string, status = 409): APIError {
  return new APIError(status, { detail: 'The provider said no.' }, code);
}

function renderQuiz() {
  return render(
    <CreditProvider>
      <QuizModal
        courseId={1}
        topics={['Sorting']}
        readyDocumentCount={2}
        onQueued={vi.fn()}
        onClose={vi.fn()}
      />
    </CreditProvider>,
  );
}

async function attemptGeneration() {
  const person = userEvent.setup();
  renderQuiz();
  await person.click(await screen.findByRole('button', { name: /start the quiz/i }));
  return person;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetCredits.mockResolvedValue(UNMETERED);
  mockEnqueue.mockResolvedValue({ job_id: 23, status: 'queued' });
});

describe('when the quiz cannot be written', () => {
  it('says the model could not be reached, and that nothing was charged', async () => {
    mockEnqueue.mockRejectedValue(generationError('provider_unavailable', 503));
    await attemptGeneration();

    expect(await screen.findByText('The AI service is down')).toBeInTheDocument();
    expect(screen.getByText(/Nothing was charged/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });

  it('suggests something shorter when the model ran out of time', async () => {
    mockEnqueue.mockRejectedValue(generationError('provider_timeout', 504));
    await attemptGeneration();

    expect(await screen.findByText('That took too long')).toBeInTheDocument();
    expect(screen.getByText(/narrowing the topic/)).toBeInTheDocument();
  });

  it('tells the student to wait a minute when the model is busy', async () => {
    mockEnqueue.mockRejectedValue(generationError('provider_rate_limited', 429));
    await attemptGeneration();

    expect(await screen.findByText('Too many requests right now')).toBeInTheDocument();
  });

  it('offers a broader topic when nothing covers the one that was asked for', async () => {
    mockEnqueue.mockRejectedValue(generationError('no_relevant_material'));
    await attemptGeneration();

    expect(await screen.findByText('Nothing on that topic')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /use every topic/i })).toBeInTheDocument();
  });

  it('separates an indexing gap from a topic that is too narrow, though both arrive as 409', async () => {
    mockEnqueue.mockRejectedValue(generationError('material_not_indexed'));
    await attemptGeneration();

    expect(await screen.findByText('Your material is not searchable yet')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /use every topic/i })).toBeNull();
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });

  it('points at the sources when none of them are ready', async () => {
    mockEnqueue.mockRejectedValue(generationError('no_ready_material'));
    await attemptGeneration();

    expect(await screen.findByText('Nothing is ready yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /see your sources/i })).toBeInTheDocument();
  });

  it('names a lost connection as being offline rather than a server fault', async () => {
    mockEnqueue.mockRejectedValue(new TypeError('Failed to fetch'));
    await attemptGeneration();

    expect(await screen.findByText('You are offline')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });

  it('offers no retry for a course that is gone', async () => {
    mockEnqueue.mockRejectedValue(new APIError(404, { detail: 'Course not found' }));
    await attemptGeneration();

    expect(await screen.findByText('This course is gone')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /try again/i })).toBeNull();
  });

  it('returns to the setup rather than to an error when the balance will not cover it', async () => {
    mockEnqueue.mockRejectedValue(new APIError(402, { detail: 'Not enough credits' }));
    await attemptGeneration();

    expect(await screen.findByRole('button', { name: /start the quiz/i })).toBeInTheDocument();
    expect(screen.queryByText('Not enough credits')).toBeNull();
  });

  it('queues the quiz again when the student retries a generation that failed', async () => {
    mockEnqueue.mockRejectedValueOnce(generationError('provider_unavailable', 503));
    const person = await attemptGeneration();

    await person.click(await screen.findByRole('button', { name: /try again/i }));

    await waitFor(() => expect(mockEnqueue).toHaveBeenCalledTimes(2));
  });
});
