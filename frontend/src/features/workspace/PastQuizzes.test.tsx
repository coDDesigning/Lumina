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

  it('shows nothing at all when no quiz has been written yet', async () => {
    mockList.mockResolvedValue([]);
    const { container } = renderList();

    await Promise.resolve();
    expect(container).toBeEmptyDOMElement();
  });

  it('stays quiet rather than shouting when the list cannot be read', async () => {
    mockList.mockRejectedValue(new Error('boom'));
    const { container } = renderList();

    await Promise.resolve();
    expect(container).toBeEmptyDOMElement();
  });
});
