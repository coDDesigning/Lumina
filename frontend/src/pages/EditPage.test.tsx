import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import type { Workspace } from '../data/workspaces';
import EditPage from './EditPage';

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

const mockWorkspace: Workspace = {
  id: '1',
  name: 'Operating Systems',
  subjectArea: '',
  educationLevel: 'unspecified',
  semester: 'Fall 2026',
  examDate: '2026-12-15',
  topics: ['Processes', 'Memory'],
  syllabus: 'Deep dive into kernels and virtual memory.',
  progress: 45,
  status: 'In progress',
  updatedAt: 'Updated today',
  accent: 'blue',
  sources: [],
};

describe('EditPage', () => {
  it('pre-populates existing course fields', () => {
    const handleSave = vi.fn();

    render(
      <MemoryRouter>
        <EditPage workspace={mockWorkspace} onSave={handleSave} />
      </MemoryRouter>,
    );

    expect(screen.getByPlaceholderText('Enter course name')).toHaveValue(
      'Operating Systems',
    );
    expect(screen.getByLabelText(/Semester/i)).toHaveValue('Fall 2026');
    expect(screen.getByPlaceholderText('Topic one, Topic two')).toHaveValue(
      'Processes, Memory',
    );
    expect(
      screen.getByPlaceholderText('Describe the course scope and learning goals'),
    ).toHaveValue('Deep dive into kernels and virtual memory.');
  });

  it('updates course details and triggers onSave with formatted data', async () => {
    const user = userEvent.setup();
    const handleSave = vi.fn();

    render(
      <MemoryRouter>
        <EditPage workspace={mockWorkspace} onSave={handleSave} />
      </MemoryRouter>,
    );

    const nameInput = screen.getByPlaceholderText('Enter course name');
    const topicsInput = screen.getByPlaceholderText('Topic one, Topic two');

    await user.clear(nameInput);
    await user.type(nameInput, 'Distributed Systems');

    await user.clear(topicsInput);
    await user.type(topicsInput, 'Raft, Paxos, Sharding');

    const saveButton = screen.getByRole('button', { name: 'Save changes' });
    await user.click(saveButton);

    expect(handleSave).toHaveBeenCalledWith(
      expect.objectContaining({
        id: '1',
        name: 'Distributed Systems',
        topics: ['Raft', 'Paxos', 'Sharding'],
      }),
    );

    expect(
      screen.getByText('Course changes saved successfully.'),
    ).toBeInTheDocument();
  });

  it('resets modified form values back to original course state', async () => {
    const user = userEvent.setup();
    const handleSave = vi.fn();

    render(
      <MemoryRouter>
        <EditPage workspace={mockWorkspace} onSave={handleSave} />
      </MemoryRouter>,
    );

    const nameInput = screen.getByPlaceholderText('Enter course name');
    await user.clear(nameInput);
    await user.type(nameInput, 'Temporary Name');

    expect(nameInput).toHaveValue('Temporary Name');

    const resetButton = screen.getByRole('button', { name: 'Reset' });
    await user.click(resetButton);

    expect(nameInput).toHaveValue('Operating Systems');
  });
});
