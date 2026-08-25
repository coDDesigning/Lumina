import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { quizAPI } from '@/api/quiz';
import type { QuizView } from '@/api/types';
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
