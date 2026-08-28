import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
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

const TRUE_FALSE: QuizQuestionView = {
  question_id: 2,
  question_number: 2,
  question_type: 'true_false',
  difficulty: 'easy',
  topic: 'Sorting',
  question: 'Quicksort is always O(n log n).',
  options: ['True', 'False'],
  correct_option_index: 1,
  correct_answer: { type: 'true_false', value: false },
  explanation: 'Its worst case is quadratic.',
};

const OPEN_ENDED: QuizQuestionView = {
  question_id: 3,
  question_number: 3,
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
  quiz: {
    quiz_id: 7,
    course_id: 1,
    questions: [MULTIPLE_CHOICE, TRUE_FALSE, OPEN_ENDED],
  },
};

function renderQuiz() {
  return render(
    <CreditProvider>
      <QuizModal courseId={1} topics={['Sorting']} readyDocumentCount={2} onClose={vi.fn()} />
    </CreditProvider>,
  );
}

async function startQuiz() {
  renderQuiz();
  await userEvent.click(await screen.findByRole('button', { name: /start the quiz/i }));
  await screen.findByText('Which sort is stable?');
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetCredits.mockResolvedValue(STATUS);
  mockGenerate.mockResolvedValue(QUIZ as never);
});

function duplicateControlNames(): string[] {
  const dialog = screen.getByRole('dialog');
  const names = Array.from(dialog.querySelectorAll('button')).map(
    (button) => button.getAttribute('aria-label') ?? button.textContent?.trim() ?? '',
  );
  const seen = new Set<string>();
  const repeated = new Set<string>();
  for (const name of names) {
    if (seen.has(name)) {
      repeated.add(name);
    }
    seen.add(name);
  }
  return Array.from(repeated);
}

describe('practising one topic', () => {
  function renderPreset(initialTopic: string) {
    return render(
      <CreditProvider>
        <QuizModal
          courseId={1}
          topics={['Sorting']}
          readyDocumentCount={2}
          initialTopic={initialTopic}
          onClose={vi.fn()}
        />
      </CreditProvider>,
    );
  }

  it('shows the topic it was opened for, even one the course never listed', async () => {
    renderPreset('Graph Algorithms');

    const select = await screen.findByLabelText(/Which topic/);
    expect(select).toHaveValue('Graph Algorithms');
    expect(
      within(select).getByRole('option', { name: 'Graph Algorithms' }),
    ).toBeInTheDocument();
  });

  it('generates against the preset topic', async () => {
    renderPreset('Graph Algorithms');

    await userEvent.click(await screen.findByRole('button', { name: /start the quiz/i }));

    await waitFor(() => expect(mockGenerate).toHaveBeenCalled());
    expect(mockGenerate.mock.calls[0][1]).toMatchObject({
      topic_focus: 'Graph Algorithms',
    });
  });

  it('lets the topic be changed before anything is generated', async () => {
    renderPreset('Graph Algorithms');

    const select = await screen.findByLabelText(/Which topic/);
    await userEvent.selectOptions(select, 'Sorting');
    await userEvent.click(screen.getByRole('button', { name: /start the quiz/i }));

    await waitFor(() => expect(mockGenerate).toHaveBeenCalled());
    expect(mockGenerate.mock.calls[0][1]).toMatchObject({ topic_focus: 'Sorting' });
  });

  it('lists a preset topic the course also lists only once', async () => {
    renderPreset('Sorting');

    const select = await screen.findByLabelText(/Which topic/);
    expect(within(select).getAllByRole('option', { name: 'Sorting' })).toHaveLength(1);
  });
});

describe('taking a quiz', () => {
  it('gives every control in the setup step its own name', async () => {
    renderQuiz();
    await screen.findByRole('button', { name: /start the quiz/i });

    expect(duplicateControlNames()).toEqual([]);
  });

  it('never shows the answer while the question is still being answered', async () => {
    await startQuiz();

    expect(screen.getByRole('radio', { name: /Merge sort/ })).toBeInTheDocument();
    expect(screen.queryByText(/preserves the order of equal keys/)).toBeNull();
    expect(screen.queryByText(/The answer/)).toBeNull();

    const options = screen.getAllByRole('radio');
    for (const option of options) {
      expect(option).not.toBeChecked();
      expect(option).not.toHaveAttribute('data-correct');
    }
  });

  it('offers multiple choice as one group of radios, not four loose buttons', async () => {
    await startQuiz();

    const radios = screen.getAllByRole('radio');
    expect(radios).toHaveLength(4);
    expect(new Set(radios.map((radio) => radio.getAttribute('name'))).size).toBe(1);
  });

  it('gives true or false its own two-way layout', async () => {
    await startQuiz();

    await userEvent.click(screen.getByRole('button', { name: /next question/i }));

    expect(screen.getByText('Quicksort is always O(n log n).')).toBeInTheDocument();
    const radios = screen.getAllByRole('radio');
    expect(radios).toHaveLength(2);
    expect(screen.getByRole('radio', { name: /True/ })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /False/ })).toBeInTheDocument();
  });

  it('gives a written question a text box instead of options', async () => {
    await startQuiz();

    await userEvent.click(screen.getByRole('button', { name: /next question/i }));
    await userEvent.click(screen.getByRole('button', { name: /next question/i }));

    expect(screen.getByText('Explain why merge sort needs extra space.')).toBeInTheDocument();
    expect(screen.queryByRole('radio')).toBeNull();
    expect(screen.getByRole('textbox', { name: /your answer/i })).toBeInTheDocument();
    expect(screen.getByText(/may come back unscored/i)).toBeInTheDocument();
  });

  it('marks in the navigator which questions have been answered', async () => {
    await startQuiz();

    const navigator = screen.getByRole('navigation', { name: 'Questions' });
    expect(
      within(navigator).getByRole('button', { name: 'Question 1, not answered' }),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole('radio', { name: /Merge sort/ }));

    expect(
      within(navigator).getByRole('button', { name: 'Question 1, answered' }),
    ).toBeInTheDocument();
    expect(
      within(navigator).getByRole('button', { name: 'Question 2, not answered' }),
    ).toBeInTheDocument();
  });

  it('warns about unanswered questions before handing in', async () => {
    await startQuiz();

    await userEvent.click(screen.getByRole('button', { name: /next question/i }));
    await userEvent.click(screen.getByRole('button', { name: /next question/i }));

    expect(screen.getByText(/3 questions are still unanswered/)).toBeInTheDocument();
  });

  it('sends the chosen option for options and the text for written answers', async () => {
    mockSubmit.mockResolvedValue({
      attempt_id: 1,
      quiz_id: 7,
      score: 1,
      correct_count: 1,
      graded_count: 1,
      total_questions: 3,
      time_spent_seconds: 30,
      created_at: '2026-08-23T10:00:00Z',
      quiz_purpose: null,
      timed: false,
      expired: false,
      answers: [],
    });

    await startQuiz();
    await userEvent.click(screen.getByRole('radio', { name: /Merge sort/ }));
    await userEvent.click(screen.getByRole('button', { name: /next question/i }));
    await userEvent.click(screen.getByRole('radio', { name: /False/ }));
    await userEvent.click(screen.getByRole('button', { name: /next question/i }));
    await userEvent.type(screen.getByRole('textbox', { name: /your answer/i }), 'It buffers.');
    await userEvent.click(screen.getByRole('button', { name: /hand it in/i }));

    await waitFor(() => expect(mockSubmit).toHaveBeenCalled());
    const [, , payload] = mockSubmit.mock.calls[0];
    expect(payload.answers).toEqual([
      { question_id: 1, selected_option_index: 1 },
      { question_id: 2, selected_option_index: 1 },
      { question_id: 3, text_response: 'It buffers.' },
    ]);
  });
});

describe('quiz results', () => {
  const ATTEMPT: QuizAttemptResponse = {
    attempt_id: 1,
    quiz_id: 7,
    score: 0.5,
    correct_count: 1,
    graded_count: 2,
    total_questions: 3,
    time_spent_seconds: 95,
    created_at: '2026-08-23T10:00:00Z',
    quiz_purpose: null,
    timed: false,
    expired: false,
    answers: [
      {
        question_id: 1,
        question_type: 'multiple_choice',
        selected_option_index: 1,
        text_response: null,
        correct_option_index: 1,
        correct_answer: null,
        is_correct: true,
        score: 1,
        feedback: null,
      },
      {
        question_id: 2,
        question_type: 'true_false',
        selected_option_index: 0,
        text_response: null,
        correct_option_index: 1,
        correct_answer: null,
        is_correct: false,
        score: 0,
        feedback: null,
      },
      {
        question_id: 3,
        question_type: 'open_ended',
        selected_option_index: null,
        text_response: 'It uses a buffer.',
        correct_option_index: null,
        correct_answer: null,
        is_correct: null,
        score: null,
        feedback: null,
      },
    ],
  };

  async function seeResults(attempt: QuizAttemptResponse = ATTEMPT) {
    mockSubmit.mockResolvedValue(attempt);
    await startQuiz();
    await userEvent.click(screen.getByRole('button', { name: /next question/i }));
    await userEvent.click(screen.getByRole('button', { name: /next question/i }));
    await userEvent.click(screen.getByRole('button', { name: /hand it in/i }));
    await screen.findByRole('heading', { name: /50%/ });
  }

  it('reads the score as a fraction of what was marked, not of everything asked', async () => {
    await seeResults();

    expect(screen.getByRole('heading', { name: /50%/ })).toBeInTheDocument();
    expect(screen.getByText(/1 of 2 marked answers correct/)).toBeInTheDocument();
  });

  it('never counts an unscored written answer as wrong', async () => {
    await seeResults();

    const rows = screen.getAllByRole('listitem');
    const writtenRow = rows[2];
    expect(within(writtenRow).getByText('Not scored')).toBeInTheDocument();
    expect(within(writtenRow).queryByText('Not correct')).toBeNull();

    expect(
      screen.getByText(/One written answer could not be marked automatically/),
    ).toBeInTheDocument();
    expect(screen.getByText(/count neither for nor against/)).toBeInTheDocument();
  });

  it('shows the right answer only for the ones that were wrong', async () => {
    await seeResults();

    const rows = screen.getAllByRole('listitem');
    const [correctRow, wrongRow] = rows;

    expect(within(correctRow).queryByText('The answer')).toBeNull();
    expect(within(wrongRow).getByText('The answer')).toBeInTheDocument();
    expect(within(wrongRow).getByText('False')).toBeInTheDocument();
  });

  it('offers a reference answer rather than a verdict for a written question', async () => {
    await seeResults();

    expect(screen.getByText('A reference answer')).toBeInTheDocument();
    expect(screen.getByText('It merges into a separate buffer.')).toBeInTheDocument();
  });

  it('says nothing was answered rather than leaving the row blank', async () => {
    await seeResults({
      ...ATTEMPT,
      answers: [
        { ...ATTEMPT.answers[0], selected_option_index: null, is_correct: false },
        ATTEMPT.answers[1],
        ATTEMPT.answers[2],
      ],
    });

    expect(screen.getByText('Nothing')).toBeInTheDocument();
  });
});
