import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { quizAPI } from '@/api/quiz';
import type { QuizSessionView, QuizView } from '@/api/types';
import type { Workspace } from '@/data/workspaces';
import QuizSessionPage from './QuizSessionPage';

vi.mock('@/api/quiz', () => ({
  quizAPI: {
    get: vi.fn(),
    getSession: vi.fn(),
    saveSessionAnswer: vi.fn(),
    submitSession: vi.fn(),
  },
}));

const WORKSPACE = {
  id: '1',
  ownerId: 1,
  name: 'Data Structures',
  topics: [],
} as unknown as Workspace;

const QUIZ: QuizView = {
  quiz_id: 9,
  course_id: 1,
  title: 'Mock exam',
  created_at: '2026-05-01T10:00:00Z',
  user_id: 1,
  model_used: null,
  generation_settings: null,
  generation_context: null,
  quiz_purpose: 'exam_mock_exam',
  exam_plan_output_id: 601,
  exam_topic_key: null,
  timed: true,
  time_limit_seconds: 3600,
  answers_hidden: true,
  questions: [
    {
      question_id: 101,
      question_number: 1,
      question_type: 'multiple_choice',
      difficulty: 'medium',
      topic: 'Graphs',
      question: 'Which traversal uses a queue?',
      options: ['DFS', 'BFS', 'Both', 'Neither'],
      correct_option_index: null,
      correct_answer: null,
      explanation: '',
      citations: [],
    },
    {
      question_id: 102,
      question_number: 2,
      question_type: 'open_ended',
      difficulty: 'medium',
      topic: 'Graphs',
      question: 'Explain why BFS finds the shortest path.',
      options: null,
      correct_option_index: null,
      correct_answer: null,
      explanation: '',
      citations: [],
    },
  ],
};

function session(overrides: Partial<QuizSessionView> = {}): QuizSessionView {
  const expires = new Date(Date.now() + 3_600_000).toISOString();
  return {
    session_id: 55,
    quiz_id: 9,
    status: 'active',
    started_at: new Date().toISOString(),
    expires_at: expires,
    time_limit_seconds: 3600,
    seconds_remaining: 3600,
    elapsed_seconds: 0,
    answered_count: 0,
    answers: [],
    attempt_id: null,
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/courses/1/practice/9/sessions/55']}>
      <Routes>
        <Route
          path="/courses/:courseId/practice/:quizId/sessions/:sessionId"
          element={<QuizSessionPage workspace={WORKSPACE} />}
        />
        <Route path="/courses/:courseId/practice/:quizId/attempts/:attemptId" element={<p>Marked</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(quizAPI.get).mockResolvedValue(QUIZ);
  vi.mocked(quizAPI.getSession).mockResolvedValue(session());
  vi.mocked(quizAPI.saveSessionAnswer).mockImplementation(async () => session({ answered_count: 1 }));
  vi.mocked(quizAPI.submitSession).mockResolvedValue({
    attempt_id: 77,
    quiz_id: 9,
    score: 1,
    correct_count: 1,
    graded_count: 1,
    total_questions: 2,
    time_spent_seconds: 120,
    created_at: '2026-05-01T11:00:00Z',
    quiz_purpose: 'exam_mock_exam',
    timed: true,
    expired: false,
    answers: [],
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('sitting a timed paper', () => {
  it('shows the clock the server issued, not one of its own', async () => {
    renderPage();

    const timer = await screen.findByRole('timer', { name: 'Time remaining' });
    // One hour from the server's deadline, never questions × a guessed minute.
    expect(timer).toHaveTextContent(/59:5\d|1:00:00|60:00/);
  });

  it('puts back the answers already saved', async () => {
    // A reload has to return the candidate to their own work.
    vi.mocked(quizAPI.getSession).mockResolvedValue(
      session({
        answered_count: 2,
        answers: [
          { question_id: 101, selected_option_index: 1, text_response: null },
          { question_id: 102, selected_option_index: null, text_response: 'Level order.' },
        ],
      }),
    );
    renderPage();

    expect(await screen.findByRole('radio', { name: /BFS/ })).toBeChecked();
    expect(screen.getByText((_, node) => node?.textContent === '2 saved')).toBeTruthy();
  });

  it('saves a chosen option the moment it is chosen', async () => {
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('radio', { name: /BFS/ }));

    await waitFor(() =>
      expect(quizAPI.saveSessionAnswer).toHaveBeenCalledWith(1, 9, 55, 101, {
        question_id: 101,
        selected_option_index: 1,
      }),
    );
  });

  it('does not write a written answer on every keystroke', async () => {
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /Question 2/ }));

    await user.type(screen.getByRole('textbox'), 'Level order');

    expect(quizAPI.saveSessionAnswer).not.toHaveBeenCalled();
  });

  it('writes a written answer when the candidate leaves it', async () => {
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /Question 2/ }));

    await user.type(screen.getByRole('textbox'), 'Level order');
    await user.tab();

    await waitFor(() =>
      expect(quizAPI.saveSessionAnswer).toHaveBeenCalledWith(1, 9, 55, 102, {
        question_id: 102,
        text_response: 'Level order',
      }),
    );
  });

  it('sends the last thing typed before handing in', async () => {
    // The answer someone is part-way through is the one most likely to be lost.
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /Question 2/ }));
    await user.type(screen.getByRole('textbox'), 'Level order');

    await user.click(screen.getByRole('button', { name: 'Hand it in' }));
    const confirm = within(await screen.findByRole('dialog'));
    await user.click(confirm.getByRole('button', { name: 'Hand it in' }));

    await waitFor(() => expect(quizAPI.submitSession).toHaveBeenCalled());
    expect(quizAPI.saveSessionAnswer).toHaveBeenCalledWith(1, 9, 55, 102, {
      question_id: 102,
      text_response: 'Level order',
    });
  });

  it('keeps every saved answer once time is up, and still hands them in', async () => {
    vi.mocked(quizAPI.getSession).mockResolvedValue(
      session({
        status: 'expired',
        seconds_remaining: 0,
        expires_at: new Date(Date.now() - 1000).toISOString(),
        answered_count: 1,
        answers: [{ question_id: 101, selected_option_index: 1, text_response: null }],
      }),
    );
    renderPage();
    const user = userEvent.setup();

    expect(await screen.findByText(/Every answer you saved is still here/)).toBeInTheDocument();
    expect(await screen.findByRole('radio', { name: /BFS/ })).toBeChecked();
    expect(screen.getByRole('radio', { name: /BFS/ })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Hand in what you saved' }));

    await waitFor(() => expect(quizAPI.submitSession).toHaveBeenCalledWith(1, 9, 55));
  });

  it('offers a retry when handing in fails, without losing the answers', async () => {
    vi.mocked(quizAPI.submitSession).mockRejectedValueOnce(new TypeError('Failed to fetch'));
    vi.mocked(quizAPI.getSession).mockResolvedValue(
      session({
        answers: [{ question_id: 101, selected_option_index: 1, text_response: null }],
        answered_count: 1,
      }),
    );
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /Question 2/ }));
    await user.click(screen.getByRole('button', { name: 'Hand it in' }));
    const confirm = within(await screen.findByRole('dialog'));
    await user.click(confirm.getByRole('button', { name: 'Hand it in' }));

    expect(await screen.findByText(/Your saved answers are safe/)).toBeInTheDocument();

    // The work is still on the page, on the question it belongs to.
    await user.click(screen.getByRole('button', { name: /Question 1/ }));
    expect(screen.getByRole('radio', { name: /BFS/ })).toBeChecked();
  });

  it('sends someone who already handed in to their result', async () => {
    vi.mocked(quizAPI.getSession).mockResolvedValue(
      session({ status: 'submitted', attempt_id: 77 }),
    );
    renderPage();

    expect(await screen.findByRole('link', { name: 'See how it went' })).toHaveAttribute(
      'href',
      '/courses/1/practice/9/attempts/77',
    );
  });
});
