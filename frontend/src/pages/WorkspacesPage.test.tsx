import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { APIError } from '../api/client';
import type { Workspace } from '../data/workspaces';
import WorkspacesPage from './WorkspacesPage';

// These suites are not about credits; an unmetered account renders no credit UI.
vi.mock('../context/CreditContext', () => ({
  useCredits: () => ({
    status: null,
    isLoading: false,
    error: null,
    refresh: vi.fn(),
    isMetered: false,
    costOf: () => null,
    canAfford: () => true,
  }),
}))


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
    subjectArea: '',
    educationLevel: 'unspecified',
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
    subjectArea: '',
    educationLevel: 'unspecified',
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
          onDelete={vi.fn()}
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
          onDelete={vi.fn()}
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
          onDelete={vi.fn()}
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
          onDelete={vi.fn()}
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
      subjectArea: '',
      educationLevel: 'unspecified',
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
          onDelete={vi.fn()}
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
        subjectArea: '',
        educationLevel: 'unspecified',
        semester: 'Fall 2026',
      }),
    );
  });

  it('submits the chosen education level and subject area', async () => {
    const user = userEvent.setup();
    const handleCreate = vi.fn().mockResolvedValue({
      id: '4',
      name: 'AP Biology',
      subjectArea: 'Biology',
      educationLevel: 'high_school',
      semester: '',
      examDate: '',
      topics: [],
      syllabus: '',
      progress: 0,
      status: 'In progress',
      updatedAt: 'Updated just now',
      accent: 'blue',
      sources: [],
    });

    render(
      <MemoryRouter>
        <WorkspacesPage
          workspaces={mockWorkspaces}
          activeWorkspaceId="1"
          onCreate={handleCreate}
          onSelect={vi.fn()}
          onDelete={vi.fn()}
        />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole('button', { name: /Create workspace/i }));

    const modal = screen.getByRole('dialog');
    await user.type(
      screen.getByPlaceholderText('e.g. Introduction to Economics'),
      'AP Biology',
    );
    await user.type(screen.getByPlaceholderText('e.g. Biology'), 'Biology');
    await user.selectOptions(
      within(modal).getByRole('combobox', { name: /Education level/i }),
      'high_school',
    );

    await user.click(
      within(modal).getByRole('button', { name: 'Create workspace' }),
    );

    expect(handleCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'AP Biology',
        subjectArea: 'Biology',
        educationLevel: 'high_school',
      }),
    );
  });

  it('renders explicit error state with retry action when course list fails', async () => {
    const user = userEvent.setup();
    const handleRetry = vi.fn();

    render(
      <MemoryRouter>
        <WorkspacesPage
          workspaces={[]}
          activeWorkspaceId=""
          error="Network connection timeout."
          onRetry={handleRetry}
          onCreate={vi.fn()}
          onSelect={vi.fn()}
          onDelete={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('Network connection timeout.');
    expect(screen.getByText('Failed to load workspaces')).toBeInTheDocument();

    const retryButton = screen.getByRole('button', { name: 'Retry' });
    await user.click(retryButton);

    expect(handleRetry).toHaveBeenCalledTimes(1);
  });

  it('preserves user input and displays error when workspace creation fails', async () => {
    const user = userEvent.setup();
    const handleCreate = vi.fn().mockRejectedValue(
      new APIError(400, { detail: 'Course title already exists.' }),
    );

    render(
      <MemoryRouter>
        <WorkspacesPage
          workspaces={mockWorkspaces}
          activeWorkspaceId="1"
          onCreate={handleCreate}
          onSelect={vi.fn()}
          onDelete={vi.fn()}
        />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole('button', { name: /Create workspace/i }));

    const nameInput = screen.getByPlaceholderText('e.g. Introduction to Economics');
    const semesterInput = screen.getByPlaceholderText('e.g. Fall 2026');

    await user.type(nameInput, 'Advanced Physics');
    await user.type(semesterInput, 'Spring 2027');

    const modal = screen.getByRole('dialog');
    const submitButton = within(modal).getByRole('button', { name: 'Create workspace' });
    await user.click(submitButton);

    expect(await screen.findByRole('alert')).toHaveTextContent('Course title already exists.');
    // Input must be preserved
    expect(nameInput).toHaveValue('Advanced Physics');
    expect(semesterInput).toHaveValue('Spring 2027');
    // Dialog must remain open
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('distinctly renders no activity state when progress is null vs actual 0% progress', () => {
    const workspacesWithMixedProgress: Workspace[] = [
      {
        ...mockWorkspaces[0],
        id: '10',
        name: 'Unstudied Course',
        progress: null,
      },
      {
        ...mockWorkspaces[1],
        id: '11',
        name: 'Zero Score Course',
        progress: 0,
      },
    ];

    render(
      <MemoryRouter>
        <WorkspacesPage
          workspaces={workspacesWithMixedProgress}
          activeWorkspaceId="10"
          onCreate={vi.fn()}
          onSelect={vi.fn()}
          onDelete={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText('No quiz activity yet')).toBeInTheDocument();
    expect(screen.getByText('0%')).toBeInTheDocument();
  });
});

const deletableWorkspace: Workspace = {
  id: '1',
  name: 'Organic Chemistry',
  subjectArea: '',
  educationLevel: 'unspecified',
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

function renderDeletablePage(
  overrides: {
    onDelete?: (workspaceId: string) => Promise<void>;
    onSelect?: (workspaceId: string) => void;
  } = {},
) {
  return render(
    <MemoryRouter>
      <WorkspacesPage
        workspaces={[deletableWorkspace]}
        activeWorkspaceId={deletableWorkspace.id}
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
    renderDeletablePage({ onDelete });

    fireEvent.click(deleteButton());

    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.getByText(/permanently erases/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Delete permanently' }));

    await waitFor(() => expect(onDelete).toHaveBeenCalledWith('1'));
  });

  it('does not delete when the confirmation is cancelled', () => {
    const onDelete = vi.fn().mockResolvedValue(undefined);
    renderDeletablePage({ onDelete });

    fireEvent.click(deleteButton());
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.queryByText(/permanently erases/i)).not.toBeInTheDocument();
  });

  it('does not open the workspace when the delete control is used', () => {
    const onSelect = vi.fn();
    renderDeletablePage({ onSelect });

    fireEvent.click(deleteButton());

    expect(onSelect).not.toHaveBeenCalled();
  });

  it('reports a failed deletion instead of dropping the card', async () => {
    const onDelete = vi
      .fn()
      .mockRejectedValue(
        new APIError(500, { detail: 'Course cleanup failed; retry hard deletion' }),
      );
    renderDeletablePage({ onDelete });

    fireEvent.click(deleteButton());
    fireEvent.click(screen.getByRole('button', { name: 'Delete permanently' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Course cleanup failed');
    expect(screen.getByText('Organic Chemistry')).toBeInTheDocument();
  });
});
