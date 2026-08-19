import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import type { Workspace } from '../data/workspaces';
import WorkspacesPage from './WorkspacesPage';

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 1,
      name: 'Student',
      email: 's@example.com',
      role: 'student',
      is_banned: false,
      credits: null,
      preferred_model: 'gemini-1.5-flash',
    },
    isAuthenticated: true,
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

const mockWorkspaces: Workspace[] = [
  {
    id: '1',
    name: 'Operating Systems',
    semester: 'Fall 2026',
    examDate: '2026-12-15',
    topics: ['Processes', 'Memory', 'Concurrency'],
    syllabus: 'Core CS syllabus',
    progress: 45,
    status: 'In progress',
    updatedAt: 'Updated today',
    accent: 'blue',
    sources: [],
  },
  {
    id: '2',
    name: 'Algorithms & Data Structures',
    semester: 'Spring 2026',
    examDate: '',
    topics: ['Graphs', 'Dynamic Programming'],
    syllabus: 'Advanced algorithms',
    progress: 80,
    status: 'In progress',
    updatedAt: 'Updated yesterday',
    accent: 'violet',
    sources: [],
  },
];

describe('WorkspacesPage', () => {
  it('renders list of workspaces with course metadata', () => {
    const handleSelect = vi.fn();
    const handleCreate = vi.fn();

    render(
      <MemoryRouter>
        <WorkspacesPage
          workspaces={mockWorkspaces}
          activeWorkspaceId="1"
          onCreate={handleCreate}
          onSelect={handleSelect}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText('Your Workspaces')).toBeInTheDocument();
    expect(screen.getByText('Operating Systems')).toBeInTheDocument();
    expect(screen.getByText('Algorithms & Data Structures')).toBeInTheDocument();
    expect(screen.getByText('45%')).toBeInTheDocument();
    expect(screen.getByText('80%')).toBeInTheDocument();
    expect(screen.getByText('2 workspaces')).toBeInTheDocument();
  });

  it('filters workspaces based on search query', async () => {
    const user = userEvent.setup();
    const handleSelect = vi.fn();
    const handleCreate = vi.fn();

    render(
      <MemoryRouter>
        <WorkspacesPage
          workspaces={mockWorkspaces}
          activeWorkspaceId="1"
          onCreate={handleCreate}
          onSelect={handleSelect}
        />
      </MemoryRouter>,
    );

    const searchInput = screen.getByPlaceholderText(
      'Search by course, semester, or topic...',
    );

    await user.type(searchInput, 'Algorithms');

    expect(screen.getByText('Algorithms & Data Structures')).toBeInTheDocument();
    expect(screen.queryByText('Operating Systems')).not.toBeInTheDocument();
    expect(screen.getByText('1 workspace')).toBeInTheDocument();
  });

  it('shows empty state when no workspaces match query and allows clearing search', async () => {
    const user = userEvent.setup();
    const handleSelect = vi.fn();
    const handleCreate = vi.fn();

    render(
      <MemoryRouter>
        <WorkspacesPage
          workspaces={mockWorkspaces}
          activeWorkspaceId="1"
          onCreate={handleCreate}
          onSelect={handleSelect}
        />
      </MemoryRouter>,
    );

    const searchInput = screen.getByPlaceholderText(
      'Search by course, semester, or topic...',
    );

    await user.type(searchInput, 'NonExistentCourse');

    expect(screen.getByText('No workspaces found')).toBeInTheDocument();

    const clearButton = screen.getByRole('button', { name: 'Clear search' });
    await user.click(clearButton);

    expect(screen.getByText('Operating Systems')).toBeInTheDocument();
    expect(screen.getByText('Algorithms & Data Structures')).toBeInTheDocument();
  });

  it('opens and closes the create workspace modal', async () => {
    const user = userEvent.setup();
    const handleSelect = vi.fn();
    const handleCreate = vi.fn();

    render(
      <MemoryRouter>
        <WorkspacesPage
          workspaces={mockWorkspaces}
          activeWorkspaceId="1"
          onCreate={handleCreate}
          onSelect={handleSelect}
        />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    const createButton = screen.getByRole('button', { name: /Create workspace/i });
    await user.click(createButton);

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByLabelText(/Course name/i)).toBeInTheDocument();

    // Close via close button
    const closeButton = screen.getByLabelText('Close create workspace form');
    await user.click(closeButton);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    // Open and close via Escape key
    await user.click(createButton);
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('creates a new workspace upon form submission', async () => {
    const user = userEvent.setup();
    const handleSelect = vi.fn();
    const handleCreate = vi.fn().mockResolvedValue({
      id: '3',
      name: 'Computer Networks',
      semester: 'Fall 2026',
      examDate: '2026-11-20',
      topics: ['TCP/IP', 'DNS'],
      syllabus: 'Networking basics',
      progress: 0,
      status: 'In progress',
      updatedAt: 'Updated just now',
      accent: 'rose',
      sources: [],
    });

    render(
      <MemoryRouter>
        <WorkspacesPage
          workspaces={mockWorkspaces}
          activeWorkspaceId="1"
          onCreate={handleCreate}
          onSelect={handleSelect}
        />
      </MemoryRouter>,
    );

    const createButton = screen.getByRole('button', { name: /Create workspace/i });
    await user.click(createButton);

    const nameInput = screen.getByPlaceholderText('e.g. Introduction to Economics');
    const semesterInput = screen.getByPlaceholderText('e.g. Fall 2026');

    await user.type(nameInput, 'Computer Networks');
    await user.type(semesterInput, 'Fall 2026');

    const modal = screen.getByRole('dialog');
    const submitButton = within(modal).getByRole('button', { name: 'Create workspace' });
    await user.click(submitButton);

    expect(handleCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'Computer Networks',
        semester: 'Fall 2026',
      }),
    );
  });
});
