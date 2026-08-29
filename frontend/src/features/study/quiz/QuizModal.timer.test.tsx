import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { quizAPI } from '@/api/quiz';
import { userAPI } from '@/api/user';
import type { CreditStatus, QuizQuestionView } from '@/api/types';
import { CreditProvider } from '@/context/CreditContext';
import { createMockQuiz, createMockQuizGenerationResult } from '@/test/mocks/api';
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

const STATUS: CreditStatus = {
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

const QUIZ = createMockQuizGenerationResult({
  quiz: createMockQuiz({ questions: [MULTIPLE_CHOICE] }),
});

beforeEach(() => {
  vi.mocked(userAPI.getCredits).mockResolvedValue(STATUS);
  vi.mocked(quizAPI.generate).mockResolvedValue(QUIZ);
});

/**
 * A practice quiz is not sat against a clock.
 *
 * The setup used to offer "Work against the clock", and the attempt screen used
 * to allow a minute a question whether or not it was asked for. Neither ever
 * reached the server -- `QuizRequest` carries no time limit and the quiz came
 * back with none -- so the countdown was invented by the browser, and the
 * checkbox promised something it could not produce.
 *
 * A paper that really is timed comes from Exam Mode, where the server issues
 * the deadline and refuses the ordinary attempt endpoint.
 */
describe('an ordinary practice quiz', () => {
  it('offers no clock to turn on', async () => {
    render(
      <CreditProvider>
        <QuizModal courseId={1} topics={['Sorting']} readyDocumentCount={2} onClose={vi.fn()} />
      </CreditProvider>,
    );

    await screen.findByRole('button', { name: /start the quiz/i });

    expect(screen.queryByRole('checkbox', { name: /against the clock/i })).toBeNull();
  });

  it('runs no countdown once the questions are on screen', async () => {
    const person = userEvent.setup();
    render(
      <CreditProvider>
        <QuizModal courseId={1} topics={['Sorting']} readyDocumentCount={2} onClose={vi.fn()} />
      </CreditProvider>,
    );

    await person.click(await screen.findByRole('button', { name: /start the quiz/i }));
    await screen.findByText('Which sort is stable?');

    expect(screen.queryByRole('timer')).toBeNull();
  });

  it('hands the quiz off to its own page when the caller owns the route', async () => {
    // Both production callers do this, which is why the modal never sits a
    // paper itself.
    const person = userEvent.setup();
    const onQuizReady = vi.fn();
    render(
      <CreditProvider>
        <QuizModal
          courseId={1}
          topics={['Sorting']}
          readyDocumentCount={2}
          onQuizReady={onQuizReady}
          onClose={vi.fn()}
        />
      </CreditProvider>,
    );

    await person.click(await screen.findByRole('button', { name: /start the quiz/i }));

    await vi.waitFor(() => expect(onQuizReady).toHaveBeenCalledWith(7));
  });
});
