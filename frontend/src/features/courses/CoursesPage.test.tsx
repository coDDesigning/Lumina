import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import type { Workspace } from '@/data/workspaces';
import CoursesPage from './CoursesPage';

vi.mock('@/context/CreditContext', () => ({
  useCredits: () => ({
    status: null,
    isLoading: false,
    error: null,
    refresh: vi.fn(),
    isMetered: false,
    costOf: () => null,
    canAfford: () => true,
  }),
}));

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 1,
      name: 'Student',
      email: 's@example.com',
      role: 'user',
      is_banned: false,
      credits: null,
      preferred_model: 'ollama:llama3.1',
      education_level: 'unspecified',
    },
    isAuthenticated: true,
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
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

function renderPage(props: Partial<React.ComponentProps<typeof CoursesPage>> = {}) {
  return render(
    <MemoryRouter>
      <CoursesPage
        workspaces={mockWorkspaces}
        onCreate={vi.fn()}
        onSelect={vi.fn()}
        onDelete={vi.fn().mockResolvedValue(undefined)}
        {...props}
      />
    </MemoryRouter>,
  );
}

const openCreate = () => screen.getByRole('button', { name: 'New course' });

describe('CoursesPage', () => {
  it('renders the courses with their metadata', () => {
    renderPage();

    expect(screen.getByRole('heading', { level: 1, name: 'Your courses' })).toBeInTheDocument();
    expect(screen.getByText('Operating Systems')).toBeInTheDocument();
    expect(screen.getByText('Algorithms & Data Structures')).toBeInTheDocument();
    expect(screen.getByText('45%')).toBeInTheDocument();
    expect(screen.getByText('80%')).toBeInTheDocument();
    expect(screen.getByText('2 courses')).toBeInTheDocument();
  });

  it('never claims a source count it cannot know', () => {
    renderPage();
    expect(screen.queryByText(/0 sources/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/sources/i)).not.toBeInTheDocument();
  });

  it('filters the list by name, term, or topic', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByLabelText('Search courses'), 'Graphs');

    expect(screen.getByText('Algorithms & Data Structures')).toBeInTheDocument();
    expect(screen.queryByText('Operating Systems')).not.toBeInTheDocument();
    expect(screen.getByText('1 course')).toBeInTheDocument();
  });

  it('offers a way back when a search matches nothing', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByLabelText('Search courses'), 'zzzz');
    expect(screen.getByText('No courses found')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Clear search' }));

    expect(screen.getByText('Operating Systems')).toBeInTheDocument();
    expect(screen.getByText('Algorithms & Data Structures')).toBeInTheDocument();
  });

  it('invites a first course when the account has none', () => {
    renderPage({ workspaces: [] });
    expect(screen.getByText('Start with one course.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create your first course' })).toBeInTheDocument();
  });

  it('opens and closes the create dialog', async () => {
    const user = userEvent.setup();
    renderPage();

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    await user.click(openCreate());
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByLabelText('Course name')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    await user.click(openCreate());
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    await user.keyboard('{Escape}');
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });

  it('creates a course from the form', async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn().mockResolvedValue({ ...mockWorkspaces[0], id: '3' });
    renderPage({ onCreate });

    await user.click(openCreate());
    await user.type(screen.getByLabelText('Course name'), 'Computer Networks');
    await user.type(screen.getByLabelText(/^Term/), 'Fall 2026');

    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Create course' }));

    await waitFor(() => {
      expect(onCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Computer Networks',
          subjectArea: '',
          educationLevel: 'unspecified',
          semester: 'Fall 2026',
        }),
      );
    });
  });

  it('submits the chosen education level and subject area', async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn().mockResolvedValue({ ...mockWorkspaces[0], id: '4' });
    renderPage({ onCreate });

    await user.click(openCreate());

    const dialog = screen.getByRole('dialog');
    await user.type(screen.getByLabelText('Course name'), 'AP Biology');
    await user.type(screen.getByLabelText(/^Subject area/), 'Biology');
    await user.selectOptions(
      within(dialog).getByRole('combobox', { name: /Education level/i }),
      'high_school',
    );
    await user.click(within(dialog).getByRole('button', { name: 'Create course' }));

    await waitFor(() => {
      expect(onCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'AP Biology',
          subjectArea: 'Biology',
          educationLevel: 'high_school',
        }),
      );
    });
  });

  it('explains a failed list load and offers a retry', async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    renderPage({ workspaces: [], error: 'Network connection timeout.', onRetry });

    expect(screen.getByRole('alert')).toHaveTextContent('Network connection timeout.');
    expect(screen.getByText("We couldn't load your courses")).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('keeps what the user typed when creation fails', async () => {
    const user = userEvent.setup();
    const onCreate = vi
      .fn()
      .mockRejectedValue(new APIError(400, { detail: 'Course title already exists.' }));
    renderPage({ onCreate });

    await user.click(openCreate());

    const nameInput = screen.getByLabelText('Course name');
    const termInput = screen.getByLabelText(/^Term/);
    await user.type(nameInput, 'Advanced Physics');
    await user.type(termInput, 'Spring 2027');

    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Create course' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Course title already exists.');
    expect(nameInput).toHaveValue('Advanced Physics');
    expect(termInput).toHaveValue('Spring 2027');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('separates no quiz activity from a genuine zero score', () => {
    renderPage({
      workspaces: [
        { ...mockWorkspaces[0], id: '10', name: 'Unstudied Course', progress: null },
        { ...mockWorkspaces[1], id: '11', name: 'Zero Score Course', progress: 0 },
      ],
    });

    expect(screen.getByText('No quiz activity yet')).toBeInTheDocument();
    expect(screen.getByText('0%')).toBeInTheDocument();
  });

  it('puts the nearest upcoming exam first', () => {
    renderPage({
      workspaces: [
        { ...mockWorkspaces[0], id: 'far', name: 'Far Exam', examDate: '2099-12-01' },
        { ...mockWorkspaces[1], id: 'soon', name: 'Soon Exam', examDate: '2099-01-05' },
      ],
    });

    const titles = screen.getAllByRole('heading', { level: 2 }).map((node) => node.textContent);
    expect(titles).toEqual(['Soon Exam', 'Far Exam']);
  });
});

const deletable: Workspace = {
  ...mockWorkspaces[0],
  id: '1',
  name: 'Organic Chemistry',
};

function renderDeletable(onDelete = vi.fn().mockResolvedValue(undefined), onSelect = vi.fn()) {
  render(
    <MemoryRouter>
      <CoursesPage
        workspaces={[deletable]}
        onCreate={vi.fn()}
        onSelect={onSelect}
        onDelete={onDelete}
      />
    </MemoryRouter>,
  );
  return { onDelete, onSelect };
}

const deleteTrigger = () => screen.getByRole('button', { name: 'Delete Organic Chemistry' });

describe('CoursesPage deletion', () => {
  it('requires confirmation, and the exact course name, before deleting', async () => {
    const user = userEvent.setup();
    const { onDelete } = renderDeletable();

    await user.click(deleteTrigger());

    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.getByText(/permanently erases/i)).toBeInTheDocument();

    const confirm = screen.getByRole('button', { name: 'Delete permanently' });
    expect(confirm).toBeDisabled();

    await user.type(screen.getByLabelText('Type Organic Chemistry to confirm'), 'Organic Chem');
    expect(confirm).toBeDisabled();

    await user.type(screen.getByLabelText('Type Organic Chemistry to confirm'), 'istry');
    expect(confirm).toBeEnabled();

    await user.click(confirm);
    await waitFor(() => expect(onDelete).toHaveBeenCalledWith('1'));
  });

  it('does not delete when the confirmation is dismissed', async () => {
    const user = userEvent.setup();
    const { onDelete } = renderDeletable();

    await user.click(deleteTrigger());
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(onDelete).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(screen.queryByText(/permanently erases/i)).not.toBeInTheDocument();
    });
  });

  it('does not open the course when the delete control is used', async () => {
    const user = userEvent.setup();
    const { onSelect } = renderDeletable();

    await user.click(deleteTrigger());

    expect(onSelect).not.toHaveBeenCalled();
  });

  it('reports a failed deletion instead of dropping the card', async () => {
    const user = userEvent.setup();
    const onDelete = vi
      .fn()
      .mockRejectedValue(
        new APIError(500, { detail: 'Course cleanup failed; retry hard deletion' }),
      );
    renderDeletable(onDelete);

    await user.click(deleteTrigger());
    await user.type(
      screen.getByLabelText('Type Organic Chemistry to confirm'),
      'Organic Chemistry',
    );
    await user.click(screen.getByRole('button', { name: 'Delete permanently' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Course cleanup failed');
    expect(screen.getByText('Organic Chemistry')).toBeInTheDocument();
  });
});
