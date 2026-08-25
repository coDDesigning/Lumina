import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { progressAPI } from '@/api/progress';
import { useCourseProgress } from './useCourseProgress';

vi.mock('@/api/progress', () => ({
  progressAPI: {
    get: vi.fn(),
    listAll: vi.fn(),
  },
}));

const mockGet = vi.mocked(progressAPI.get);

function Reader({ label }: { label: string }) {
  const { progress, isLoading } = useCourseProgress(7);
  return (
    <p data-testid={label}>{isLoading ? 'loading' : (progress?.attempts_count ?? 'none')}</p>
  );
}

describe('course progress sharing', () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockGet.mockResolvedValue({
      status: 'practiced',
      attempts_count: 7,
      average_score: 0.8,
      topic_mastery: [],
    });
  });

  it('reads one course once no matter how many screens ask for it', async () => {
    render(
      <>
        <Reader label="workspace" />
        <Reader label="progress" />
      </>,
    );

    await waitFor(() => expect(screen.getByTestId('workspace')).toHaveTextContent('7'));
    expect(screen.getByTestId('progress')).toHaveTextContent('7');
    expect(mockGet).toHaveBeenCalledTimes(1);
  });

  it('does not refetch when navigating from the workspace to progress and back', async () => {
    const workspace = render(<Reader label="workspace" />);
    await waitFor(() => expect(screen.getByTestId('workspace')).toHaveTextContent('7'));
    workspace.unmount();

    const progress = render(<Reader label="progress" />);
    await waitFor(() => expect(screen.getByTestId('progress')).toHaveTextContent('7'));
    progress.unmount();

    render(<Reader label="workspace-again" />);
    await waitFor(() => expect(screen.getByTestId('workspace-again')).toHaveTextContent('7'));

    expect(mockGet).toHaveBeenCalledTimes(1);
  });

  it('still passes an abort signal through to the API', async () => {
    render(<Reader label="workspace" />);
    await waitFor(() => expect(screen.getByTestId('workspace')).toHaveTextContent('7'));

    expect(mockGet).toHaveBeenCalledWith(7, expect.objectContaining({ signal: expect.anything() }));
  });
});
