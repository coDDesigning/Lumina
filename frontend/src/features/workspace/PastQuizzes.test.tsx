import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { quizAPI } from '@/api/quiz';
import type { QuizSummary } from '@/api/types';
import { PastQuizzes } from './PastQuizzes';

vi.mock('@/api/quiz', () => ({ quizAPI: { list: vi.fn() } }));

const mockList = vi.mocked(quizAPI.list);

function quiz(overrides: Partial<QuizSummary> = {}): QuizSummary {
  return {
    quiz_id: 4,
    course_id: 1,
    title: 'Graph traversals',
    question_count: 10,
    created_at: '2026-08-22T13:00:00Z',
    user_id: 1,
    model_used: 'gemini-3.6-flash',
    generation_settings: null,
    generation_context: null,
    ...overrides,
  };
}

function renderList() {
  return render(
    <MemoryRouter>
      <PastQuizzes courseId={1} workspaceId="1" />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('PastQuizzes', () => {
  it('shows an explicit loading state while fetching quizzes', () => {
    mockList.mockReturnValue(new Promise(() => {}));
    renderList();

    expect(screen.getByRole('status', { name: 'Loading saved quizzes' })).toBeInTheDocument();
  });

  it('shows an explicit empty state when no quiz has been saved yet', async () => {
    mockList.mockResolvedValue([]);
    renderList();

    expect(await screen.findByText('No quizzes saved yet. Generate a practice quiz to see it here.')).toBeInTheDocument();
  });

  it('shows Not attempted yet when a quiz has no attempts', async () => {
    mockList.mockResolvedValue([quiz({ best_score: null, last_score: null })]);
    renderList();

    expect(await screen.findByText('Graph traversals')).toBeInTheDocument();
    expect(screen.getByText(/Not attempted yet/i)).toBeInTheDocument();
  });

  it('shows best and last score summaries when attempts exist', async () => {
    mockList.mockResolvedValue([quiz({ attempts_count: 2, best_score: 1.0, last_score: 0.5 })]);
    renderList();

    expect(await screen.findByText('Graph traversals')).toBeInTheDocument();
    expect(screen.getByText(/Best:/i)).toBeInTheDocument();
    expect(screen.getByText('100%')).toBeInTheDocument();
    expect(screen.getByText(/Last:/i)).toBeInTheDocument();
    expect(screen.getByText('50%')).toBeInTheDocument();
  });

  it('offers a written quiz again, and says it costs nothing', async () => {
    mockList.mockResolvedValue([quiz()]);
    renderList();

    expect(await screen.findByText('Graph traversals')).toBeInTheDocument();
    expect(screen.getByText(/costs nothing/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Take it again' })).toHaveAttribute(
      'href',
      '/courses/1/practice/4',
    );
  });

  it('counts the questions the way a person would say it', async () => {
    mockList.mockResolvedValue([quiz({ question_count: 1 })]);
    renderList();

    expect(await screen.findByText((_, node) => node?.textContent?.includes('1 question') === true && node.tagName === 'SPAN')).toBeInTheDocument();
  });

  it('shows empty state rather than breaking when the list cannot be read', async () => {
    mockList.mockRejectedValue(new Error('boom'));
    renderList();

    expect(await screen.findByText('No quizzes saved yet. Generate a practice quiz to see it here.')).toBeInTheDocument();
  });

  it('lists every quiz as its own row', async () => {
    mockList.mockResolvedValue([
      quiz({ quiz_id: 4, title: 'Graph traversals' }),
      quiz({ quiz_id: 3, title: 'Shortest paths' }),
    ]);
    renderList();

    expect(await screen.findByText('Graph traversals')).toBeInTheDocument();
    expect(screen.getByText('Shortest paths')).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
  });
});
