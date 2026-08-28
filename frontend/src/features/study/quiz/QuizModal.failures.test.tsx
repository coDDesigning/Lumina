import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { quizAPI } from '@/api/quiz';
import { userAPI } from '@/api/user';
import type { CreditStatus, QuizAttemptResponse, QuizQuestionView } from '@/api/types';
import { CreditProvider } from '@/context/CreditContext';
import { QuizModal } from './QuizModal';

vi.mock('@/api/quiz', () => ({
  quizAPI: { generate: vi.fn(), submitAttempt: vi.fn() },
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

const mockGenerate = vi.mocked(quizAPI.generate);
const mockSubmit = vi.mocked(quizAPI.submitAttempt);
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

const MULTIPLE_CHOICE: QuizQuestionView = {
  question_id: 1,
  question_number: 1,
  question_type: 'multiple_choice',
  difficulty: 'medium',
  topic: 'Sorting',
  question: 'Which sort is stable?',
  options: ['Quicksort', 'Merge sort', 'Heapsort', 'Selection sort'],
  correct_option_index: 1,
  correct_answer: { type: 'multiple_choice', option_index: 1 },
  explanation: 'Merge sort preserves the order of equal keys.',
};

const QUIZ = { quiz: { quiz_id: 7, course_id: 1, questions: [MULTIPLE_CHOICE] } };

const ATTEMPT: QuizAttemptResponse = {
  attempt_id: 1,
  quiz_id: 7,
  score: 1,
  correct_count: 1,
  graded_count: 1,
  total_questions: 1,
  time_spent_seconds: 30,
  created_at: '2026-08-23T10:00:00Z',
  quiz_purpose: null,
  timed: false,
  expired: false,
  answers: [],
};

function generationError(code: string, status = 409): APIError {
  return new APIError(status, { detail: 'The provider said no.' }, code);
}

function renderQuiz() {
  return render(
    <CreditProvider>
      <QuizModal courseId={1} topics={['Sorting']} readyDocumentCount={2} onClose={vi.fn()} />
    </CreditProvider>,
  );
}

async function attemptGeneration() {
  const person = userEvent.setup();
  renderQuiz();
  await person.click(await screen.findByRole('button', { name: /start the quiz/i }));
  return person;
}

async function solveThenSubmit() {
  const person = userEvent.setup();
  renderQuiz();
  await person.click(await screen.findByRole('button', { name: /start the quiz/i }));
  await screen.findByText('Which sort is stable?');
  await person.click(screen.getByRole('radio', { name: /Merge sort/ }));
  await person.click(screen.getByRole('button', { name: /hand it in/i }));
  return person;
}

beforeEach(() => {
  mockGetCredits.mockResolvedValue(UNMETERED);
  mockGenerate.mockResolvedValue(QUIZ as never);
  mockSubmit.mockResolvedValue(ATTEMPT);
});

describe('when the quiz cannot be written', () => {
  it('says the model could not be reached, and that nothing was charged', async () => {
    mockGenerate.mockRejectedValue(generationError('provider_unavailable', 503));
    await attemptGeneration();

    expect(await screen.findByText('The AI service is down')).toBeInTheDocument();
    expect(screen.getByText(/Nothing was charged/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });

  it('suggests something shorter when the model ran out of time', async () => {
    mockGenerate.mockRejectedValue(generationError('provider_timeout', 504));
    await attemptGeneration();

    expect(await screen.findByText('That took too long')).toBeInTheDocument();
    expect(screen.getByText(/narrowing the topic/)).toBeInTheDocument();
  });

  it('tells the student to wait a minute when the model is busy', async () => {
    mockGenerate.mockRejectedValue(generationError('provider_rate_limited', 429));
    await attemptGeneration();

    expect(await screen.findByText('Too many requests right now')).toBeInTheDocument();
  });

  it('offers a broader topic when nothing covers the one that was asked for', async () => {
    mockGenerate.mockRejectedValue(generationError('no_relevant_material'));
    await attemptGeneration();

    expect(await screen.findByText('Nothing on that topic')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /use every topic/i })).toBeInTheDocument();
  });

  it('separates an indexing gap from a topic that is too narrow, though both arrive as 409', async () => {
    mockGenerate.mockRejectedValue(generationError('material_not_indexed'));
    await attemptGeneration();

    expect(await screen.findByText('Your material is not searchable yet')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /use every topic/i })).toBeNull();
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });

  it('points at the sources when none of them are ready', async () => {
    mockGenerate.mockRejectedValue(generationError('no_ready_material'));
    await attemptGeneration();

    expect(await screen.findByText('Nothing is ready yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /see your sources/i })).toBeInTheDocument();
  });

  it('names a lost connection as being offline rather than a server fault', async () => {
    mockGenerate.mockRejectedValue(new TypeError('Failed to fetch'));
    await attemptGeneration();

    expect(await screen.findByText('You are offline')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });

  it('offers no retry for a course that is gone', async () => {
    mockGenerate.mockRejectedValue(new APIError(404, { detail: 'Course not found' }));
    await attemptGeneration();

    expect(await screen.findByText('This course is gone')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /try again/i })).toBeNull();
  });

  it('returns to the setup rather than to an error when the balance will not cover it', async () => {
    mockGenerate.mockRejectedValue(new APIError(402, { detail: 'Not enough credits' }));
    await attemptGeneration();

    expect(await screen.findByRole('button', { name: /start the quiz/i })).toBeInTheDocument();
    expect(screen.queryByText('Not enough credits')).toBeNull();
  });

  it('writes a fresh quiz when the student retries a generation that failed', async () => {
    mockGenerate.mockRejectedValueOnce(generationError('provider_unavailable', 503));
    const person = await attemptGeneration();

    await person.click(await screen.findByRole('button', { name: /try again/i }));

    expect(await screen.findByText('Which sort is stable?')).toBeInTheDocument();
    expect(mockGenerate).toHaveBeenCalledTimes(2);
  });
});

describe('when the answers cannot be handed in', () => {
  it('says the answers were not saved rather than blaming the quiz', async () => {
    mockSubmit.mockRejectedValue(new APIError(503, { detail: 'upstream unavailable' }));
    await solveThenSubmit();

    expect(await screen.findByText('Your answers were not saved')).toBeInTheDocument();
  });

  it('hands the same answers in again rather than starting a new quiz', async () => {
    mockSubmit.mockRejectedValueOnce(new APIError(503, { detail: 'upstream unavailable' }));
    const person = await solveThenSubmit();

    await person.click(await screen.findByRole('button', { name: /try again/i }));

    await waitFor(() => expect(mockSubmit).toHaveBeenCalledTimes(2));
    expect(mockGenerate).toHaveBeenCalledTimes(1);

    const [, , retried] = mockSubmit.mock.calls[1];
    expect(retried.answers).toEqual([{ question_id: 1, selected_option_index: 1 }]);
  });

  it('offers no retry when handing in again cannot help', async () => {
    mockSubmit.mockRejectedValue(new APIError(400, { detail: 'That attempt is not valid.' }));
    await solveThenSubmit();

    expect(await screen.findByText('Your answers were not saved')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /try again/i })).toBeNull();
  });
});
