import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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

const OPEN_ENDED: QuizQuestionView = {
  question_id: 2,
  question_number: 2,
  question_type: 'open_ended',
  difficulty: 'hard',
  topic: 'Sorting',
  question: 'Explain why merge sort needs extra space.',
  options: null,
  correct_option_index: null,
  correct_answer: { type: 'open_ended', reference_answer: 'It merges into a separate buffer.' },
  explanation: 'The merge step cannot be done in place efficiently.',
};

const QUIZ = {
  quiz: { quiz_id: 7, course_id: 1, questions: [MULTIPLE_CHOICE, OPEN_ENDED] },
};

const ATTEMPT: QuizAttemptResponse = {
  attempt_id: 1,
  quiz_id: 7,
  score: 0.5,
  correct_count: 1,
  graded_count: 2,
  total_questions: 2,
  time_spent_seconds: 120,
  created_at: '2026-08-23T10:00:00Z',
  answers: [],
};

const SECONDS_PER_QUESTION = 60;
const TOTAL_SECONDS = QUIZ.quiz.questions.length * SECONDS_PER_QUESTION;

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function tick(seconds: number) {
  for (let second = 0; second < seconds; second += 1) {
    await advance(1000);
  }
}

function secondsLeft(): number {
  const name = screen.getByRole('timer').getAttribute('aria-label') ?? '';
  const [, clock] = name.split('Time remaining: ');
  const [minutes, seconds] = clock.split(':');
  return Number(minutes) * 60 + Number(seconds);
}

async function startQuiz(options: { withTimer?: boolean } = {}) {
  const person = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  const view = render(
    <CreditProvider>
      <QuizModal courseId={1} topics={['Sorting']} readyDocumentCount={2} onClose={vi.fn()} />
    </CreditProvider>,
  );

  const start = await screen.findByRole('button', { name: /start the quiz/i });
  if (options.withTimer === false) {
    await person.click(screen.getByRole('checkbox', { name: /against the clock/i }));
  }
  await person.click(start);
  await screen.findByText('Which sort is stable?');

  return { person, view };
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  mockGetCredits.mockResolvedValue(STATUS);
  mockGenerate.mockResolvedValue(QUIZ as never);
  mockSubmit.mockResolvedValue(ATTEMPT);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('the clock', () => {
  it('allows a minute for every question it asked', async () => {
    await startQuiz();

    expect(secondsLeft()).toBeLessThanOrEqual(TOTAL_SECONDS);
    expect(secondsLeft()).toBeGreaterThan(TOTAL_SECONDS - SECONDS_PER_QUESTION);
  });

  it('counts down while the student reads', async () => {
    await startQuiz();
    const before = secondsLeft();

    await tick(10);

    expect(before - secondsLeft()).toBeGreaterThanOrEqual(10);
  });

  it('keeps counting while the student is choosing an answer', async () => {
    const { person } = await startQuiz();
    const before = secondsLeft();

    for (let round = 0; round < 8; round += 1) {
      await advance(600);
      await person.click(screen.getByRole('radio', { name: /Merge sort/ }));
      await advance(600);
      await person.click(screen.getByRole('radio', { name: /Quicksort/ }));
    }

    expect(before - secondsLeft()).toBeGreaterThanOrEqual(5);
  });

  it('keeps counting while the student is typing', async () => {
    const { person } = await startQuiz();

    await person.click(screen.getByRole('button', { name: /next question/i }));
    const box = screen.getByRole('textbox', { name: /your answer/i });
    const before = secondsLeft();

    for (const character of 'abcdefghijklmnop') {
      await advance(600);
      await person.type(box, character);
    }

    expect(before - secondsLeft()).toBeGreaterThanOrEqual(5);
  });

  it('hands the quiz in by itself when the time runs out', async () => {
    await startQuiz();

    await tick(TOTAL_SECONDS);

    expect(mockSubmit).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole('heading', { name: /50%/ })).toBeInTheDocument();
  });

  it('runs no clock at all when the student turns it off', async () => {
    await startQuiz({ withTimer: false });

    expect(screen.queryByRole('timer')).toBeNull();

    await tick(TOTAL_SECONDS + 5);

    expect(mockSubmit).not.toHaveBeenCalled();
  });
});

describe('the wait while answers are marked', () => {
  it('reports how long the marking has actually taken', async () => {
    let release: (attempt: QuizAttemptResponse) => void = () => {};
    mockSubmit.mockReturnValue(
      new Promise<QuizAttemptResponse>((resolve) => {
        release = resolve;
      }) as never,
    );

    const { person } = await startQuiz();
    await person.click(screen.getByRole('radio', { name: /Merge sort/ }));
    await person.click(screen.getByRole('button', { name: /next question/i }));
    await person.click(screen.getByRole('button', { name: /hand it in/i }));

    const marking = await screen.findByText('Marking your answers');
    const panel = marking.parentElement as HTMLElement;

    await tick(4);

    expect(panel.textContent).toMatch(/[1-9]\d*s/);

    release(ATTEMPT);
  });
});
