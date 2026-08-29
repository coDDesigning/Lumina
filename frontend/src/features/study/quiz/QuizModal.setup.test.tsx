import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { quizAPI } from '@/api/quiz';
import { settingsAPI } from '@/api/settings';
import { userAPI } from '@/api/user';
import type { CourseSettings, CreditStatus, QuizQuestionView } from '@/api/types';
import { CreditProvider } from '@/context/CreditContext';
import { createMockQuiz, createMockQuizGenerationResult } from '@/test/mocks/api';
import { MAX_ANSWER_TEXT_CHARS, OPEN_ENDED_ROWS, SHORT_ANSWER_ROWS } from './answerDraft';
import { QuizModal } from './QuizModal';

vi.mock('@/api/quiz', () => ({
  quizAPI: { generate: vi.fn(), submitAttempt: vi.fn() },
}));

vi.mock('@/api/settings', () => ({
  settingsAPI: { get: vi.fn(), update: vi.fn() },
}));

vi.mock('@/api/user', () => ({
  userAPI: { getCredits: vi.fn() },
}));

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: true, user: { id: 1 } }),
}));

const mockGenerate = vi.mocked(quizAPI.generate);
const mockSettings = vi.mocked(settingsAPI.get);
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

const METERED: CreditStatus = {
  credits: 40,
  metering_enabled: true,
  email_verification_required: false,
  is_email_verified: true,
  monthly_grant: 20,
  balance_cap: 100,
  next_grant_at: '2026-09-01T00:00:00Z',
  generation_costs: { quiz: 1, quiz_open_ended: 2, flashcard: 1, study_guide: 1 },
};

const SETTINGS = {
  study_mode: 'practice',
  difficulty: 'medium',
  question_count: 5,
  summary_length: 'medium',
  detail_level: 'standard',
} satisfies CourseSettings;

function question(overrides: Partial<QuizQuestionView>): QuizQuestionView {
  return {
    question_id: 1,
    question_number: 1,
    question_type: 'multiple_choice',
    difficulty: 'medium',
    topic: 'Sorting',
    question: 'Which sort is stable?',
    options: ['Quicksort', 'Merge sort'],
    correct_option_index: 1,
    correct_answer: { type: 'multiple_choice', option_index: 1 },
    explanation: 'Merge sort preserves the order of equal keys.',
    ...overrides,
  };
}

function quizOf(...questions: QuizQuestionView[]) {
  return createMockQuizGenerationResult({ quiz: createMockQuiz({ questions }) });
}

function renderQuiz(props: Partial<{ onQuizReady: (quizId: number) => void }> = {}) {
  const person = userEvent.setup();
  render(
    <CreditProvider>
      <QuizModal
        courseId={1}
        topics={['Sorting']}
        readyDocumentCount={2}
        onClose={vi.fn()}
        {...props}
      />
    </CreditProvider>,
  );
  return person;
}

beforeEach(() => {
  mockGetCredits.mockResolvedValue(UNMETERED);
  mockSettings.mockResolvedValue(SETTINGS);
  mockGenerate.mockResolvedValue(quizOf(question({})));
});

describe('choosing what to be asked', () => {
  it('offers every question type the backend can write', async () => {
    renderQuiz();

    await screen.findByRole('button', { name: /start the quiz/i });
    for (const label of ['Multiple choice', 'True or false', 'Short answer', 'Written answer']) {
      expect(screen.getByRole('checkbox', { name: new RegExp(label, 'i') })).toBeInTheDocument();
    }
  });

  it('sends only the types that were ticked', async () => {
    const person = renderQuiz();

    await person.click(await screen.findByRole('checkbox', { name: /short answer/i }));
    await person.click(screen.getByRole('button', { name: /start the quiz/i }));

    await waitFor(() => expect(mockGenerate).toHaveBeenCalled());
    expect(mockGenerate.mock.calls[0][1]).toMatchObject({
      question_types: ['multiple_choice', 'short_answer'],
    });
  });

  it('refuses to leave the quiz with no question type at all', async () => {
    const person = renderQuiz();

    const only = await screen.findByRole('checkbox', { name: /multiple choice/i });
    await person.click(only);

    expect(only).toBeChecked();

    await person.click(screen.getByRole('button', { name: /start the quiz/i }));
    await waitFor(() => expect(mockGenerate).toHaveBeenCalled());
    expect(mockGenerate.mock.calls[0][1]).toMatchObject({
      question_types: ['multiple_choice'],
    });
  });

  it('sends the count, topic and difficulty the student picked', async () => {
    const person = renderQuiz();

    await person.selectOptions(await screen.findByLabelText(/How many questions/), '15');
    await person.selectOptions(screen.getByLabelText(/Which topic/), 'Sorting');
    await person.selectOptions(screen.getByLabelText(/How hard/), 'hard');
    await person.click(screen.getByRole('button', { name: /start the quiz/i }));

    await waitFor(() => expect(mockGenerate).toHaveBeenCalled());
    expect(mockGenerate.mock.calls[0][1]).toMatchObject({
      question_count: 15,
      topic_focus: 'Sorting',
      difficulty: 'hard',
    });
  });
});

describe('the defaults the course already saved', () => {
  it('opens on the difficulty the course settings recorded', async () => {
    mockSettings.mockResolvedValue({ ...SETTINGS, difficulty: 'hard', question_count: 10 });
    renderQuiz();

    await waitFor(() => expect(screen.getByLabelText(/How hard/)).toHaveValue('hard'));
    expect(screen.getByLabelText(/How many questions/)).toHaveValue('10');
  });

  it('rounds a stored count up to one the quiz actually offers', async () => {
    mockSettings.mockResolvedValue({ ...SETTINGS, difficulty: 'easy', question_count: 12 });
    renderQuiz();

    await waitFor(() => expect(screen.getByLabelText(/How many questions/)).toHaveValue('15'));
  });

  it('falls back to the largest count rather than an option that does not exist', async () => {
    mockSettings.mockResolvedValue({ ...SETTINGS, question_count: 500 });
    renderQuiz();

    await waitFor(() => expect(screen.getByLabelText(/How many questions/)).toHaveValue('20'));
  });
});

describe('what a quiz costs', () => {
  it('prices a written-answer quiz higher, because grading is prepaid', async () => {
    mockGetCredits.mockResolvedValue(METERED);
    const person = renderQuiz();

    expect(await screen.findByText(/This quiz costs 1\./)).toBeInTheDocument();

    await person.click(screen.getByRole('checkbox', { name: /written answer/i }));

    expect(await screen.findByText(/marked by the model, so this quiz costs 2\./)).toBeInTheDocument();
  });

  it('quotes no price at all to an unmetered account', async () => {
    renderQuiz();

    await screen.findByRole('button', { name: /start the quiz/i });
    expect(screen.queryByText(/this quiz costs/i)).toBeNull();
  });
});

describe('answering in writing', () => {
  it('gives a short answer a small box and a written answer a large one', async () => {
    mockGenerate.mockResolvedValue(
      quizOf(
        question({
          question_id: 3,
          question_type: 'short_answer',
          question: 'Name a stable sort.',
          options: null,
          correct_option_index: null,
          correct_answer: { type: 'short_answer', text: 'Merge sort', accepted_answers: ['Merge sort'] },
        }),
        question({
          question_id: 4,
          question_number: 2,
          question_type: 'open_ended',
          question: 'Explain why merge sort needs extra space.',
          options: null,
          correct_option_index: null,
          correct_answer: { type: 'open_ended', reference_answer: 'It buffers.' },
        }),
      ),
    );
    const person = renderQuiz();

    await person.click(await screen.findByRole('button', { name: /start the quiz/i }));
    await screen.findByText('Name a stable sort.');

    const short = screen.getByRole('textbox', { name: /your answer/i });
    expect(short).toHaveAttribute('rows', String(SHORT_ANSWER_ROWS));
    expect(short).toHaveAttribute('maxlength', String(MAX_ANSWER_TEXT_CHARS));

    await person.click(screen.getByRole('button', { name: /next question/i }));

    const written = screen.getByRole('textbox', { name: /your answer/i });
    expect(written).toHaveAttribute('rows', String(OPEN_ENDED_ROWS));
    expect(written).toHaveAttribute('maxlength', String(MAX_ANSWER_TEXT_CHARS));
  });

  it('warns only about a written answer that the model may not be able to mark', async () => {
    mockGenerate.mockResolvedValue(
      quizOf(
        question({
          question_id: 3,
          question_type: 'short_answer',
          question: 'Name a stable sort.',
          options: null,
          correct_option_index: null,
          correct_answer: { type: 'short_answer', text: 'Merge sort', accepted_answers: ['Merge sort'] },
        }),
      ),
    );
    const person = renderQuiz();

    await person.click(await screen.findByRole('button', { name: /start the quiz/i }));
    await screen.findByText('Name a stable sort.');

    expect(screen.queryByText(/may come back unscored/i)).toBeNull();
  });
});

describe('practising from somewhere else in the app', () => {
  it('hands the quiz id back instead of solving it in place', async () => {
    const onQuizReady = vi.fn();
    const person = renderQuiz({ onQuizReady });

    await person.click(await screen.findByRole('button', { name: /start the quiz/i }));

    await waitFor(() => expect(onQuizReady).toHaveBeenCalledWith(7));
    expect(screen.queryByText('Which sort is stable?')).toBeNull();
  });
});

describe('with nothing to be asked about', () => {
  it('offers no setup controls at all, and says why', async () => {
    render(
      <CreditProvider>
        <QuizModal courseId={1} topics={['Sorting']} readyDocumentCount={0} onClose={vi.fn()} />
      </CreditProvider>,
    );

    expect(await screen.findByText('There is nothing to work from yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /start the quiz/i })).toBeDisabled();
    expect(screen.queryByLabelText(/How many questions/)).toBeNull();
  });
});

describe('naming the controls', () => {
  it('gives the topic list every topic the course carries, once each', async () => {
    render(
      <CreditProvider>
        <QuizModal
          courseId={1}
          topics={['Sorting', 'sorting', 'Graphs']}
          readyDocumentCount={2}
          onClose={vi.fn()}
        />
      </CreditProvider>,
    );

    const select = await screen.findByLabelText(/Which topic/);
    expect(within(select).getAllByRole('option', { name: /^sorting$/i })).toHaveLength(1);
    expect(within(select).getByRole('option', { name: 'Graphs' })).toBeInTheDocument();
  });
});
