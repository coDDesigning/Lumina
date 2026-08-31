import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { activityAPI } from '@/api/activity';
import { adsAPI } from '@/api/ads';
import { APIError } from '@/api/client';
import { coursesAPI } from '@/api/courses';
import type { Workspace } from '@/data/workspaces';
import { AD_CONSENT_STORAGE_KEY } from '@/features/ads/useAdConsent';
import CoursesPage from './CoursesPage';

vi.mock('@/api/activity', () => ({
  activityAPI: { list: vi.fn().mockResolvedValue([]) },
}));

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
      is_email_verified: true,
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
    progress: {
      averageScore: 45,
      timeSpentSeconds: null,
      lastActivity: '2026-08-22T10:00:00Z',
      status: 'practiced',
    },
    updatedAt: 'Updated today',
    accent: 'blue',
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
    progress: {
      averageScore: 80,
      timeSpentSeconds: null,
      lastActivity: '2026-08-20T10:00:00Z',
      status: 'mastered',
    },
    updatedAt: 'Updated yesterday',
    accent: 'violet',
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

  it('gives every course a reachable settings control', () => {
    renderPage();
    expect(
      screen.getByRole('button', { name: 'Settings for Operating Systems' }),
    ).toBeInTheDocument();
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

  it('creates a course whose topic contains a comma without splitting it', async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn().mockResolvedValue({ ...mockWorkspaces[0], id: '5' });
    renderPage({ onCreate });

    await user.click(openCreate());
    await user.type(screen.getByLabelText('Course name'), 'Data Structures');

    const topics = screen.getByLabelText(/^Topics/);
    await user.type(topics, 'Trees, Heaps and Priority Queues{Enter}');
    await user.type(topics, 'Shortest Paths{Enter}');

    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Create course' }));

    await waitFor(() => {
      expect(onCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Data Structures',
          topics: ['Trees, Heaps and Priority Queues', 'Shortest Paths'],
        }),
      );
    });
  });

  it('keeps a topic that was typed but never confirmed with Enter', async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn().mockResolvedValue({ ...mockWorkspaces[0], id: '6' });
    renderPage({ onCreate });

    await user.click(openCreate());
    await user.type(screen.getByLabelText('Course name'), 'Compilers');
    await user.type(screen.getByLabelText(/^Topics/), 'Parsing');

    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Create course' }));

    await waitFor(() => {
      expect(onCreate).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'Compilers', topics: ['Parsing'] }),
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

  it('fills the syllabus field from an uploaded file', async () => {
    const user = userEvent.setup();
    const extract = vi
      .spyOn(coursesAPI, 'extractSyllabus')
      .mockResolvedValue({ text: 'Week 1: Limits', truncated: false });
    renderPage();

    await user.click(openCreate());

    const file = new File(['Week 1: Limits'], 'syllabus.pdf', {
      type: 'application/pdf',
    });
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    await user.upload(fileInput, file);

    await waitFor(() => {
      expect(screen.getByLabelText(/^Syllabus/)).toHaveValue('Week 1: Limits');
    });
    expect(extract).toHaveBeenCalledWith(file);
    expect(screen.getByText('Read syllabus.pdf.')).toBeInTheDocument();
    extract.mockRestore();
  });

  it('says so when an uploaded syllabus holds no readable text', async () => {
    const user = userEvent.setup();
    const extract = vi
      .spyOn(coursesAPI, 'extractSyllabus')
      .mockResolvedValue({ text: '', truncated: false });
    renderPage();

    await user.click(openCreate());

    const file = new File([''], 'scan.pdf', { type: 'application/pdf' });
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    await user.upload(fileInput, file);

    expect(
      await screen.findByText('No text could be read from scan.pdf.'),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/^Syllabus/)).toHaveValue('');
    extract.mockRestore();
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

  it('shows the status the backend derived and when the course was last studied', () => {
    renderPage({
      workspaces: [
        {
          ...mockWorkspaces[0],
          id: '20',
          name: 'Mastered Course',
          progress: {
            averageScore: 91,
            timeSpentSeconds: null,
            lastActivity: '2026-08-22T10:00:00Z',
            status: 'mastered',
          },
        },
        {
          ...mockWorkspaces[1],
          id: '21',
          name: 'Untouched Course',
          progress: {
            averageScore: null,
            timeSpentSeconds: null,
            lastActivity: null,
            status: 'ready',
          },
        },
      ],
    });

    expect(screen.getByText('Mastered')).toBeInTheDocument();
    expect(screen.getByText(/Last studied/)).toBeInTheDocument();
    expect(screen.getByText('Ready to study')).toBeInTheDocument();
    expect(screen.getByText('Not studied yet')).toBeInTheDocument();
  });

  it('reads a course with nothing uploaded apart from one that is ready', () => {
    renderPage({
      workspaces: [
        {
          ...mockWorkspaces[0],
          id: '30',
          name: 'Empty Course',
          progress: {
            averageScore: null,
            timeSpentSeconds: null,
            lastActivity: null,
            status: 'no_documents',
          },
        },
        {
          ...mockWorkspaces[1],
          id: '31',
          name: 'Indexing Course',
          progress: {
            averageScore: null,
            timeSpentSeconds: null,
            lastActivity: null,
            status: 'processing',
          },
        },
      ],
    });

    expect(screen.getByText('No sources ready')).toBeInTheDocument();
    expect(screen.getByText('No sources to study yet')).toBeInTheDocument();
    expect(screen.getByText('Processing')).toBeInTheDocument();
    expect(screen.getByText('Sources still processing')).toBeInTheDocument();
    expect(screen.queryByText('No quiz activity yet')).not.toBeInTheDocument();
  });

  it('shows time spent practising only when an attempt recorded it', () => {
    renderPage({
      workspaces: [
        {
          ...mockWorkspaces[0],
          id: '40',
          name: 'Timed Course',
          progress: {
            averageScore: 60,
            timeSpentSeconds: 4320,
            lastActivity: '2026-08-22T10:00:00Z',
            status: 'practiced',
          },
        },
        {
          ...mockWorkspaces[1],
          id: '41',
          name: 'Untimed Course',
          progress: {
            averageScore: 60,
            timeSpentSeconds: null,
            lastActivity: '2026-08-22T10:00:00Z',
            status: 'practiced',
          },
        },
      ],
    });

    expect(screen.getByText('1h 12m spent practising')).toBeInTheDocument();
    expect(screen.getAllByText(/spent practising/)).toHaveLength(1);
  });

  it('claims nothing about a course whose progress could not be loaded', () => {
    renderPage({
      workspaces: [
        { ...mockWorkspaces[0], id: '22', name: 'Unknown Course', progress: null },
      ],
    });

    expect(screen.getByText('Progress unavailable')).toBeInTheDocument();
    expect(screen.queryByText('Ready to study')).not.toBeInTheDocument();
    expect(screen.queryByText(/Last studied/)).not.toBeInTheDocument();
    expect(screen.queryByText('Not studied yet')).not.toBeInTheDocument();
    expect(screen.queryByText(/spent practising/)).not.toBeInTheDocument();
  });

  it('separates no quiz activity from a genuine zero score and from unavailable data', () => {
    renderPage({
      workspaces: [
        {
          ...mockWorkspaces[0],
          id: '10',
          name: 'Unstudied Course',
          progress: {
            averageScore: null,
            timeSpentSeconds: null,
            lastActivity: null,
            status: 'ready',
          },
        },
        {
          ...mockWorkspaces[1],
          id: '11',
          name: 'Zero Score Course',
          progress: {
            averageScore: 0,
            timeSpentSeconds: null,
            lastActivity: '2026-08-22T10:00:00Z',
            status: 'practiced',
          },
        },
        { ...mockWorkspaces[0], id: '12', name: 'Unknown Course', progress: null },
      ],
    });

    expect(screen.getByText('No quiz activity yet')).toBeInTheDocument();
    expect(screen.getByText('0%')).toBeInTheDocument();
    expect(screen.getByText('Progress unavailable')).toBeInTheDocument();
  });

  it('counts down to an upcoming exam and says so once, not twice', () => {
    const inFiveDays = new Date(Date.now() + 5 * 24 * 60 * 60 * 1000)
      .toISOString()
      .slice(0, 10);

    renderPage({
      workspaces: [{ ...mockWorkspaces[0], name: 'Thermodynamics', examDate: inFiveDays }],
    });

    expect(screen.getByText('Exam in 5 days')).toBeInTheDocument();
    expect(screen.getByText('Your nearest exam is Thermodynamics, in 5 days.')).toBeInTheDocument();
  });

  it('labels a past exam rather than showing a bare date', () => {
    const lastWeek = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);

    renderPage({
      workspaces: [{ ...mockWorkspaces[0], name: 'Statics', examDate: lastWeek }],
    });

    expect(screen.getByText(/^Exam was /)).toBeInTheDocument();
    expect(screen.getByText('Pick a course to carry on, or start a new one.')).toBeInTheDocument();
  });

  it('previews recent activity and offers the full history', async () => {
    vi.mocked(activityAPI.list).mockResolvedValueOnce([
      {
        kind: 'attempt',
        action_type: 'quiz_attempt',
        course_id: 4,
        course_title: 'Organic Chemistry',
        occurred_at: new Date().toISOString(),
        output_id: null,
        quiz_id: 3,
        attempt_id: 9,
        topic: null,
        score: 0.6,
      },
    ]);

    renderPage();

    const activity = await screen.findByRole('link', { name: /Quiz attempt/ });
    expect(activity).toHaveAttribute('href', '/courses/4/practice/3/attempts/9');
    expect(screen.getByRole('link', { name: 'See all activity' })).toHaveAttribute(
      'href',
      '/activity',
    );
  });

  it('puts the nearest upcoming exam first', () => {
    renderPage({
      workspaces: [
        { ...mockWorkspaces[0], id: 'far', name: 'Far Exam', examDate: '2099-12-01' },
        { ...mockWorkspaces[1], id: 'soon', name: 'Soon Exam', examDate: '2099-01-05' },
      ],
    });

    const titles = within(screen.getByRole('list', { name: 'Your courses' }))
      .getAllByRole('heading', { level: 2 })
      .map((node) => node.textContent);
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

describe('CoursesPage archive filtering', () => {
  it('filters active and archived courses when archived courses exist', async () => {
    const user = userEvent.setup();
    const mixedWorkspaces: Workspace[] = [
      {
        id: '1',
        name: 'Active Biology',
        subjectArea: '',
        educationLevel: 'unspecified',
        semester: 'Fall 2026',
        examDate: '',
        topics: ['Cells'],
        syllabus: '',
        progress: null,
        updatedAt: 'Today',
        accent: 'blue',
        isArchived: false,
      },
      {
        id: '2',
        name: 'Archived Chemistry',
        subjectArea: '',
        educationLevel: 'unspecified',
        semester: 'Spring 2025',
        examDate: '',
        topics: ['Molecules'],
        syllabus: '',
        progress: null,
        updatedAt: 'Last year',
        accent: 'violet',
        isArchived: true,
      },
    ];

    render(
      <MemoryRouter>
        <CoursesPage
          workspaces={mixedWorkspaces}
          onCreate={vi.fn()}
          onSelect={vi.fn()}
          onDelete={vi.fn().mockResolvedValue(undefined)}
        />
      </MemoryRouter>,
    );

    // Active view by default
    expect(screen.getByText('Active Biology')).toBeInTheDocument();
    expect(screen.queryByText('Archived Chemistry')).not.toBeInTheDocument();

    // Switch to archived view
    const filterSelect = screen.getByLabelText('Status filter');
    await user.selectOptions(filterSelect, 'archived');

    expect(screen.queryByText('Active Biology')).not.toBeInTheDocument();
    expect(screen.getByText('Archived Chemistry')).toBeInTheDocument();
  });

  it('renders ad slot on dashboard when ads are enabled and consent is granted', async () => {
    localStorage.setItem(AD_CONSENT_STORAGE_KEY, 'granted');
    vi.spyOn(adsAPI, 'getConfig').mockResolvedValue({
      enabled: true,
      provider: 'ethicalads',
      publisher_id: 'lumina-test',
    });

    render(
      <MemoryRouter>
        <CoursesPage
          workspaces={mockWorkspaces}
          onCreate={vi.fn()}
          onSelect={vi.fn()}
          onDelete={vi.fn().mockResolvedValue(undefined)}
        />
      </MemoryRouter>,
    );

    const slot = await screen.findByText('Ad');
    expect(slot).toBeInTheDocument();
    expect(document.getElementById('lumina-ad-slot-dashboard')).toBeInTheDocument();
  });
});
