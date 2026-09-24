import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { activityAPI } from '@/api/activity';
import type { ActivityItem } from '@/api/types';
import ActivityPage from './ActivityPage';

vi.mock('@/api/activity', () => ({
  activityAPI: { list: vi.fn() },
}));

const list = vi.mocked(activityAPI.list);

function item(overrides: Partial<ActivityItem>): ActivityItem {
  return {
    kind: 'generation',
    action_type: 'exam_plan',
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

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/activity']}>
      <ActivityPage />
    </MemoryRouter>,
  );
}

describe('ActivityPage', () => {
  beforeEach(() => {
    list.mockReset();
  });

  it('puts the summary above the history and reads both from one request', async () => {
    list.mockResolvedValue([
      item({ output_id: 12 }),
      item({
        kind: 'attempt',
        action_type: 'quiz_attempt',
        output_id: null,
        attempt_id: 3,
        score: 0.6,
      }),
    ]);

    renderPage();

    const summary = await screen.findByRole('region', { name: 'Summary' });
    const history = screen.getByRole('region', { name: 'History' });
    const order = summary.compareDocumentPosition(history);
    expect(order & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(summary).getByText('60%')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Today' })).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(1);
  });

  it('shows no summary when there is nothing to summarise', async () => {
    list.mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText('Nothing studied yet')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Summary' })).not.toBeInTheDocument();
  });
});
