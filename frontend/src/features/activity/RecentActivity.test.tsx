import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { activityAPI } from '@/api/activity';
import type { ActivityItem } from '@/api/types';
import { RecentActivity } from './RecentActivity';

vi.mock('@/api/activity', () => ({
  activityAPI: { list: vi.fn() },
}));

const list = vi.mocked(activityAPI.list);

function item(overrides: Partial<ActivityItem>): ActivityItem {
  return {
    kind: 'generation',
    action_type: 'study_guide',
    course_id: 7,
    course_title: 'Computer Architecture',
    occurred_at: new Date().toISOString(),
    output_id: 12,
    quiz_id: null,
    attempt_id: null,
    topic: null,
    score: null,
    ...overrides,
  };
}

function renderActivity(props: { limit?: number } = {}) {
  return render(
    <MemoryRouter>
      <RecentActivity {...props} />
    </MemoryRouter>,
  );
}

describe('RecentActivity', () => {
  beforeEach(() => {
    list.mockReset();
  });

  it('names the course, what was done and when', async () => {
    list.mockResolvedValue([item({})]);

    renderActivity();

    expect(await screen.findByText('Study guide')).toBeInTheDocument();
    expect(screen.getByText('Computer Architecture')).toBeInTheDocument();
    expect(screen.getByText('today')).toBeInTheDocument();
  });

  it('opens the stored guide a generation produced', async () => {
    list.mockResolvedValue([item({ output_id: 12 })]);

    renderActivity();

    const link = await screen.findByRole('link', { name: /Study guide/ });
    expect(link).toHaveAttribute('href', '/courses/7?artifact=12');
  });

  it('opens the attempt an attempt event refers to', async () => {
    list.mockResolvedValue([
      item({
        kind: 'attempt',
        action_type: 'quiz_attempt',
        output_id: null,
        quiz_id: 3,
        attempt_id: 9,
        score: 0.8,
      }),
    ]);

    renderActivity();

    const link = await screen.findByRole('link', { name: /Quiz attempt/ });
    expect(link).toHaveAttribute('href', '/courses/7/practice/3/attempts/9');
    expect(screen.getByText('80%')).toBeInTheDocument();
  });

  it('shows the topic a generation was focused on', async () => {
    list.mockResolvedValue([item({ topic: 'Graph Algorithms' })]);

    renderActivity();

    expect(await screen.findByText('Graph Algorithms')).toBeInTheDocument();
  });

  it('asks for only as many items as it shows', async () => {
    list.mockResolvedValue([]);

    renderActivity({ limit: 5 });

    await waitFor(() => expect(list).toHaveBeenCalledWith(5, expect.anything()));
  });

  it('says so when there is nothing yet', async () => {
    list.mockResolvedValue([]);

    renderActivity();

    expect(await screen.findByText('Nothing studied yet')).toBeInTheDocument();
  });

  it('reports a failed read instead of an empty list', async () => {
    list.mockRejectedValue(new Error('network down'));

    renderActivity();

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /activity could not be loaded/i,
    );
  });
});
