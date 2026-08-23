import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import type { CourseProgressResponse } from '@/api/types';
import { ProgressView } from './ProgressView';

const SAMPLE_PROGRESS: CourseProgressResponse = {
  attempts_count: 2,
  average_score: 0.75,
  topic_mastery: [
    {
      topic: 'Algebra',
      questions_answered: 4,
      questions_correct: 3,
      mastery_percentage: 75,
      status: 'In Progress',
    },
  ],
  weak_topics: [],
  quiz_history: [
    {
      attempt_id: 101,
      quiz_id: 5,
      score: 0.5,
      correct_count: 1,
      total_questions: 2,
      time_spent_seconds: 40,
      created_at: '2026-08-23T10:00:00Z',
    },
    {
      attempt_id: 102,
      quiz_id: 5,
      score: 1.0,
      correct_count: 2,
      total_questions: 2,
      time_spent_seconds: 35,
      created_at: '2026-08-23T11:00:00Z',
    },
  ],
};

describe('ProgressView', () => {
  it('renders empty state when attempts_count is 0', () => {
    render(
      <MemoryRouter>
        <ProgressView
          courseId="10"
          documentCount={2}
          readyDocumentCount={2}
          progress={{
            attempts_count: 0,
            average_score: null,
            topic_mastery: [],
            weak_topics: [],
            quiz_history: [],
          }}
          isLoading={false}
          error={null}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText('Nothing measured yet')).toBeInTheDocument();
  });

  it('renders stats, topic mastery, and clickable attempt history links', () => {
    render(
      <MemoryRouter>
        <ProgressView
          courseId="10"
          documentCount={3}
          readyDocumentCount={3}
          progress={SAMPLE_PROGRESS}
          isLoading={false}
          error={null}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText('75%')).toBeInTheDocument();
    expect(screen.getByText('Algebra')).toBeInTheDocument();
    expect(screen.getByText('Every attempt')).toBeInTheDocument();

    const links = screen.getAllByRole('link');
    const attemptLinks = links.filter((l) =>
      l.getAttribute('href')?.includes('/practice/5/attempts/'),
    );
    expect(attemptLinks).toHaveLength(2);
    expect(attemptLinks[0]).toHaveAttribute('href', '/courses/10/practice/5/attempts/101');
    expect(attemptLinks[1]).toHaveAttribute('href', '/courses/10/practice/5/attempts/102');
  });

  it('renders error alert when error prop is provided', () => {
    render(
      <MemoryRouter>
        <ProgressView
          courseId="10"
          documentCount={1}
          readyDocumentCount={1}
          progress={null}
          isLoading={false}
          error="Network error"
        />
      </MemoryRouter>,
    );

    expect(screen.getByText('Network error')).toBeInTheDocument();
    expect(screen.getByText('Your progress could not be loaded')).toBeInTheDocument();
  });
});
