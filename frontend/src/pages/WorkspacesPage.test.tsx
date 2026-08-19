import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '../api/client';
import type { Workspace } from '../data/workspaces';
import WorkspacesPage from './WorkspacesPage';

vi.mock('../components/WorkspaceNavigation', () => ({
  default: () => null,
}));

afterEach(cleanup);

const workspace: Workspace = {
  id: '1',
  name: 'Organic Chemistry',
  semester: 'Fall 2026',
  examDate: '2026-12-01',
  topics: ['Alkanes'],
  syllabus: '',
  sources: [],
  progress: 40,
  status: 'Active',
  accent: 'blue',
  updatedAt: 'Updated today',
};

function renderPage(overrides: {
  onDelete?: (workspaceId: string) => Promise<void>;
  onSelect?: (workspaceId: string) => void;
} = {}) {
  return render(
    <MemoryRouter>
      <WorkspacesPage
        workspaces={[workspace]}
        activeWorkspaceId={workspace.id}
        onCreate={vi.fn()}
        onSelect={overrides.onSelect ?? vi.fn()}
        onDelete={overrides.onDelete ?? vi.fn().mockResolvedValue(undefined)}
      />
    </MemoryRouter>,
  );
}

const deleteButton = () =>
  screen.getByRole('button', { name: 'Delete Organic Chemistry' });

describe('WorkspacesPage deletion', () => {
  it('requires a confirmation before deleting', async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined);
    renderPage({ onDelete });

    fireEvent.click(deleteButton());

    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.getByText(/permanently erases/i)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Delete permanently' }));

    await waitFor(() => expect(onDelete).toHaveBeenCalledWith('1'));
  });

  it('does not delete when the confirmation is cancelled', () => {
    const onDelete = vi.fn().mockResolvedValue(undefined);
    renderPage({ onDelete });

    fireEvent.click(deleteButton());
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.queryByText(/permanently erases/i)).toBeNull();
  });

  it('does not open the workspace when the delete control is used', () => {
    const onSelect = vi.fn();
    renderPage({ onSelect });

    fireEvent.click(deleteButton());

    expect(onSelect).not.toHaveBeenCalled();
  });

  it('reports a failed deletion instead of dropping the card', async () => {
    const onDelete = vi
      .fn()
      .mockRejectedValue(
        new APIError(500, { detail: 'Course cleanup failed; retry hard deletion' }),
      );
    renderPage({ onDelete });

    fireEvent.click(deleteButton());
    fireEvent.click(screen.getByRole('button', { name: 'Delete permanently' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Course cleanup failed');
    expect(screen.getByText('Organic Chemistry')).toBeTruthy();
  });
});
