import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { quizAPI } from '@/api/quiz';
import type { QuizAttemptResponse, QuizView } from '@/api/types';
import type { Workspace } from '@/data/workspaces';
import QuizAttemptPage from './QuizAttemptPage';

vi.mock('@/api/quiz', () => ({
  quizAPI: {
    get: vi.fn(),
    submitAttempt: vi.fn(),
  },
}));

const mockGet = vi.mocked(quizAPI.get);

const WORKSPACE: Workspace = {
  id: '10',
  name: 'Linear Algebra',
  subjectArea: 'Mathematics',
  educationLevel: 'undergraduate',
  semester: 'Fall',
  examDate: '2026-12-01',
  topics: ['Matrices'],
  syllabus: 'Vectors.',
  progress: {
    averageScore: 50,
    timeSpentSeconds: null,
    lastActivity: null,
    status: 'practiced',
  },
  updatedAt: 'Updated today',
  accent: 'blue',
};

function quizWith(count: number): QuizView {
  return {
    quiz_id: 3,
    title: 'Practice quiz',
    questions: Array.from({ length: count }, (_, position) => ({
      question_id: position + 1,
      question: `Question number ${position + 1}`,
      question_type: 'short_answer',
      topic: 'Matrices',
      options: null,
    })),
  } as unknown as QuizView;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/courses/10/practice/3']}>
      <Routes>
        <Route
          path="/courses/:courseId/practice/:quizId"
          element={<QuizAttemptPage workspace={WORKSPACE} />}
        />
        <Route
          path="/courses/:courseId/practice/:quizId/attempts/:attemptId"
          element={<p>Attempt recorded</p>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('QuizAttemptPage question navigator', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockResolvedValue(quizWith(12));
  });

  it('is a single tab stop however many questions there are', async () => {
    renderPage();
    await screen.findByRole('navigation', { name: 'Questions' });

    const pips = screen.getAllByRole('button', { name: /^Question \d+/ });
    expect(pips).toHaveLength(12);

    const reachable = pips.filter((pip) => pip.getAttribute('tabindex') !== '-1');
    expect(reachable).toHaveLength(1);
    expect(reachable[0]).toHaveAccessibleName('Question 1, not answered');
  });

  it('moves between questions with the arrow keys', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('navigation', { name: 'Questions' });

    const first = screen.getByRole('button', { name: 'Question 1, not answered' });
    first.focus();

    await user.keyboard('{ArrowRight}');
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Question 2, not answered' })).toHaveFocus(),
    );
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Question number 2');

    await user.keyboard('{ArrowLeft}');
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Question 1, not answered' })).toHaveFocus(),
    );
  });

  it('jumps to the first and last question with Home and End', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('navigation', { name: 'Questions' });

    screen.getByRole('button', { name: 'Question 1, not answered' }).focus();

    await user.keyboard('{End}');
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Question 12, not answered' })).toHaveFocus(),
    );

    await user.keyboard('{Home}');
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Question 1, not answered' })).toHaveFocus(),
    );
  });

  it('wraps around at both ends', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('navigation', { name: 'Questions' });

    screen.getByRole('button', { name: 'Question 1, not answered' }).focus();

    await user.keyboard('{ArrowLeft}');
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Question 12, not answered' })).toHaveFocus(),
    );
  });
});

function multipleChoiceQuiz(count: number): QuizView {
  return {
    quiz_id: 3,
    title: 'Practice quiz',
    questions: Array.from({ length: count }, (_, position) => ({
      question_id: position + 1,
      question: `Question number ${position + 1}`,
      question_type: 'multiple_choice',
      topic: 'Matrices',
      options: ['First', 'Second', 'Third', 'Fourth'],
    })),
  } as unknown as QuizView;
}

const ATTEMPT: QuizAttemptResponse = {
  attempt_id: 88,
  quiz_id: 3,
  score: 1,
  correct_count: 2,
  graded_count: 2,
  total_questions: 2,
  time_spent_seconds: 30,
  created_at: '2026-08-23T10:00:00Z',
  quiz_purpose: 'practice',
  timed: false,
  expired: false,
  answers: [],
};

describe('QuizAttemptPage keyboard-only completion', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockResolvedValue(multipleChoiceQuiz(2));
    vi.mocked(quizAPI.submitAttempt).mockResolvedValue(ATTEMPT);
  });

  it('answers and hands in the whole quiz without a single pointer event', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('radio', { name: 'First' });

    for (let question = 1; question <= 2; question += 1) {
      const chosen = screen.getByRole('radio', { name: 'Second' });
      chosen.focus();
      await user.keyboard(' ');
      expect(chosen).toBeChecked();

      const advance = screen.getByRole('button', {
        name: question === 2 ? 'Hand it in' : 'Next question',
      });
      advance.focus();
      await user.keyboard('{Enter}');
    }

    await waitFor(() => {
      expect(vi.mocked(quizAPI.submitAttempt)).toHaveBeenCalledTimes(1);
    });
    const [, , payload] = vi.mocked(quizAPI.submitAttempt).mock.calls[0];
    expect(payload.answers).toEqual([
      { question_id: 1, selected_option_index: 1 },
      { question_id: 2, selected_option_index: 1 },
    ]);
  });
});

describe('answering an ordinary practice quiz', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockResolvedValue(multipleChoiceQuiz(3));
    vi.mocked(quizAPI.submitAttempt).mockResolvedValue(ATTEMPT);
  });

  it('never shows the answer while the question is still being answered', async () => {
    renderPage();
    await screen.findByRole('radio', { name: 'First' });

    for (const option of screen.getAllByRole('radio')) {
      expect(option).not.toBeChecked();
      expect(option).not.toHaveAttribute('data-correct');
    }
  });

  it('marks in the navigator which questions have been answered', async () => {
    renderPage();
    const navigator = await screen.findByRole('navigation', { name: 'Questions' });

    expect(
      within(navigator).getByRole('button', { name: 'Question 1, not answered' }),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole('radio', { name: 'Second' }));

    expect(
      within(navigator).getByRole('button', { name: 'Question 1, answered' }),
    ).toBeInTheDocument();
    expect(
      within(navigator).getByRole('button', { name: 'Question 2, not answered' }),
    ).toBeInTheDocument();
  });

  it('warns about unanswered questions before handing in', async () => {
    renderPage();
    await screen.findByRole('radio', { name: 'First' });

    await userEvent.click(screen.getByRole('button', { name: /next question/i }));
    await userEvent.click(screen.getByRole('button', { name: /next question/i }));
    await userEvent.click(screen.getByRole('button', { name: /hand it in/i }));

    expect(await screen.findByText(/3 questions are still unanswered/)).toBeInTheDocument();
  });
});

describe('when the answers cannot be handed in', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockResolvedValue(multipleChoiceQuiz(1));
  });

  it('names why the answers were not saved and keeps the student on the questions', async () => {
    vi.mocked(quizAPI.submitAttempt).mockRejectedValue(new TypeError('Failed to fetch'));
    renderPage();
    await screen.findByRole('radio', { name: 'First' });

    await userEvent.click(screen.getByRole('radio', { name: 'Second' }));
    await userEvent.click(screen.getByRole('button', { name: /hand it in/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/network error/i);
    expect(screen.getByRole('radio', { name: 'Second' })).toBeChecked();
    expect(screen.queryByText('Attempt recorded')).not.toBeInTheDocument();
  });

  it('hands the same answers in again rather than losing them', async () => {
    vi.mocked(quizAPI.submitAttempt)
      .mockRejectedValueOnce(new APIError(503, { detail: 'upstream unavailable' }))
      .mockResolvedValue(ATTEMPT);
    renderPage();
    await screen.findByRole('radio', { name: 'First' });

    await userEvent.click(screen.getByRole('radio', { name: 'Second' }));
    await userEvent.click(screen.getByRole('button', { name: /hand it in/i }));
    await screen.findByRole('alert');
    await userEvent.click(screen.getByRole('button', { name: /hand it in/i }));

    await waitFor(() => {
      expect(vi.mocked(quizAPI.submitAttempt)).toHaveBeenCalledTimes(2);
    });
    const [, , retried] = vi.mocked(quizAPI.submitAttempt).mock.calls[1];
    expect(retried.answers).toEqual([{ question_id: 1, selected_option_index: 1 }]);
    expect(mockGet).toHaveBeenCalledTimes(1);
  });
});

describe('sources while the student is still answering', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('never names the passage a question came from before it is answered', async () => {
    // Showing the source mid-attempt would point straight at the answer.
    const quiz = quizWith(1);
    quiz.questions[0].citations = [
      {
        key: 'S1',
        document_id: '11111111-1111-1111-1111-111111111111',
        document_label: 'Lecture 4',
        page_start: 12,
        page_end: 12,
      },
    ];
    mockGet.mockResolvedValue(quiz);

    renderPage();
    await screen.findByText('Question number 1');

    expect(screen.queryByText(/Lecture 4/)).not.toBeInTheDocument();
    expect(screen.queryByText('Sources:')).not.toBeInTheDocument();
  });
});
