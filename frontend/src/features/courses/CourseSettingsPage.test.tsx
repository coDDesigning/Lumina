import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { settingsAPI } from '@/api/settings';
import type { Workspace } from '@/data/workspaces';
import { ToastProvider } from '@/ui/ToastProvider';
import CourseSettingsPage from './CourseSettingsPage';

vi.mock('@/api/settings', () => ({
  settingsAPI: {
    get: vi.fn(),
    update: vi.fn(),
  },
}));

const mockGet = vi.mocked(settingsAPI.get);
const mockUpdate = vi.mocked(settingsAPI.update);

const workspace: Workspace = {
  id: '1',
  name: 'Operating Systems',
  subjectArea: 'Computer Engineering',
  educationLevel: 'undergraduate',
  semester: 'Fall 2026',
  examDate: '2026-12-15',
  topics: ['Processes', 'Memory'],
  syllabus: 'Deep dive into kernels and virtual memory.',
  progress: 45,
  status: 'In progress',
  updatedAt: 'Updated today',
  accent: 'blue',
};

const settingsPayload = {
  study_mode: 'Exam',
  difficulty: 'Adaptive',
  question_count: 12,
  summary_length: 'Medium',
  detail_level: 'Balanced',
};

function renderPage(
  overrides: {
    onSave?: (next: Workspace) => Promise<void> | void;
    onDelete?: (id: string) => Promise<void>;
  } = {},
) {
  return render(
    <ToastProvider>
      <MemoryRouter initialEntries={['/workspaces/1/settings']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/settings"
            element={
              <CourseSettingsPage
                workspace={workspace}
                onSave={overrides.onSave ?? vi.fn()}
                onDelete={overrides.onDelete ?? vi.fn().mockResolvedValue(undefined)}
              />
            }
          />
          <Route path="/dashboard" element={<h1>Courses</h1>} />
        </Routes>
      </MemoryRouter>
    </ToastProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGet.mockResolvedValue(settingsPayload);
  mockUpdate.mockResolvedValue(settingsPayload);
});

describe('CourseSettingsPage — course details', () => {
  it('pre-populates every field from the course', () => {
    renderPage();

    expect(screen.getByLabelText('Course name')).toHaveValue('Operating Systems');
    expect(screen.getByLabelText(/^Term/)).toHaveValue('Fall 2026');
    expect(screen.getByLabelText(/^Topics/)).toHaveValue('Processes, Memory');
    expect(screen.getByLabelText(/^Syllabus/)).toHaveValue(
      'Deep dive into kernels and virtual memory.',
    );
    expect(screen.getByLabelText(/^Subject area/)).toHaveValue('Computer Engineering');
  });

  it('saves trimmed values and splits topics into a list', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    renderPage({ onSave });

    const name = screen.getByLabelText('Course name');
    const topics = screen.getByLabelText(/^Topics/);

    await user.clear(name);
    await user.type(name, 'Distributed Systems');
    await user.clear(topics);
    await user.type(topics, 'Raft, Paxos, Sharding');

    await user.click(screen.getByRole('button', { name: 'Save details' }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({
          id: '1',
          name: 'Distributed Systems',
          topics: ['Raft', 'Paxos', 'Sharding'],
        }),
      );
    });

    expect(await screen.findByText('Course details saved')).toBeInTheDocument();
  });

  it('restores the original values on reset', async () => {
    const user = userEvent.setup();
    renderPage();

    const name = screen.getByLabelText('Course name');
    await user.clear(name);
    await user.type(name, 'Temporary Name');
    expect(name).toHaveValue('Temporary Name');

    await user.click(screen.getByRole('button', { name: 'Reset details' }));
    expect(name).toHaveValue('Operating Systems');
  });

  it('reports a rejected save without losing the edit', async () => {
    const user = userEvent.setup();
    const onSave = vi
      .fn()
      .mockRejectedValue(new APIError(400, { detail: 'Course title already exists.' }));
    renderPage({ onSave });

    const name = screen.getByLabelText('Course name');
    await user.clear(name);
    await user.type(name, 'Clashing Name');
    await user.click(screen.getByRole('button', { name: 'Save details' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Course title already exists.');
    expect(name).toHaveValue('Clashing Name');
  });
});

describe('CourseSettingsPage — generation defaults', () => {
  it('loads the stored defaults for this course', async () => {
    renderPage();

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith(1);
    });
    expect(await screen.findByLabelText('Questions per quiz')).toHaveValue(12);
  });

  it('sends the defaults back in the shape the API expects', async () => {
    const user = userEvent.setup();
    renderPage();

    const questionCount = await screen.findByLabelText('Questions per quiz');
    await user.clear(questionCount);
    await user.type(questionCount, '20');

    await user.click(screen.getByRole('button', { name: 'Save defaults' }));

    await waitFor(() => {
      expect(mockUpdate).toHaveBeenCalledWith(1, {
        study_mode: 'Exam',
        difficulty: 'Adaptive',
        question_count: 20,
        summary_length: 'Medium',
        detail_level: 'Balanced',
      });
    });
  });

  it('resets to what the server stored, not to the built-in defaults', async () => {
    const user = userEvent.setup();
    renderPage();

    const questionCount = await screen.findByLabelText('Questions per quiz');
    expect(questionCount).toHaveValue(12);

    await user.clear(questionCount);
    await user.type(questionCount, '20');
    expect(questionCount).toHaveValue(20);

    await user.click(screen.getByRole('button', { name: 'Reset defaults' }));

    // 12 is what this course had stored; 10 is the built-in default it must not fall back to.
    expect(questionCount).toHaveValue(12);
  });

  it('states the real range, including that a quiz caps lower than the setting', async () => {
    renderPage();
    expect(
      await screen.findByText('Between 5 and 50 here. A single quiz generates up to 20.'),
    ).toBeInTheDocument();
  });

  it('explains a failed load instead of showing stale defaults silently', async () => {
    mockGet.mockRejectedValue(new APIError(500, { detail: 'Settings unavailable' }));
    renderPage();

    expect(await screen.findByRole('alert')).toHaveTextContent('Settings unavailable');
  });
});

describe('CourseSettingsPage — deleting the course', () => {
  it('requires the exact course name before deleting, then returns to the list', async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn().mockResolvedValue(undefined);
    renderPage({ onDelete });

    await user.click(screen.getByRole('button', { name: 'Delete Operating Systems' }));

    const dialog = screen.getByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: 'Delete permanently' });
    expect(confirm).toBeDisabled();

    await user.type(
      screen.getByLabelText('Type Operating Systems to confirm'),
      'Operating Systems',
    );
    expect(confirm).toBeEnabled();

    await user.click(confirm);

    await waitFor(() => expect(onDelete).toHaveBeenCalledWith('1'));
    expect(await screen.findByRole('heading', { name: 'Courses' })).toBeInTheDocument();
  });

  it('keeps the user on the page when deletion fails', async () => {
    const user = userEvent.setup();
    const onDelete = vi
      .fn()
      .mockRejectedValue(new APIError(500, { detail: 'Course cleanup failed' }));
    renderPage({ onDelete });

    await user.click(screen.getByRole('button', { name: 'Delete Operating Systems' }));
    await user.type(
      screen.getByLabelText('Type Operating Systems to confirm'),
      'Operating Systems',
    );
    await user.click(screen.getByRole('button', { name: 'Delete permanently' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Course cleanup failed');
    expect(screen.queryByRole('heading', { name: 'Courses' })).not.toBeInTheDocument();
  });
});
