import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { progressAPI } from '@/api/progress';
import { quizAPI } from '@/api/quiz';
import { userAPI } from '@/api/user';
import type { CreditStatus } from '@/api/types';
import type { Workspace } from '@/data/workspaces';
import { CreditProvider } from '@/context/CreditContext';
import ProgressPage from './ProgressPage';

vi.mock('@/api/progress', () => ({ progressAPI: { get: vi.fn(), listAll: vi.fn() } }));

vi.mock('@/api/quiz', () => ({
  quizAPI: { list: vi.fn(), generate: vi.fn(), enqueue: vi.fn(), submitAttempt: vi.fn() },
}));

vi.mock('@/api/settings', () => ({
  settingsAPI: { get: vi.fn().mockResolvedValue({ difficulty: 'medium', question_count: 5 }) },
}));

vi.mock('@/api/user', () => ({ userAPI: { getCredits: vi.fn() } }));

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: true, user: { id: 1 } }),
}));

vi.mock('@/hooks/useCourseDocuments', () => ({
  useCourseDocuments: () => ({
    entries: [],
    isLoading: false,
    listError: null,
    readyCount: 2,
    reload: vi.fn(),
    addUploaded: vi.fn(),
    retryDocument: vi.fn(),
    deleteDocument: vi.fn(),
  }),
}));

const mockProgress = vi.mocked(progressAPI.get);
const mockQuizList = vi.mocked(quizAPI.list);
const mockEnqueue = vi.mocked(quizAPI.enqueue);
const mockGetCredits = vi.mocked(userAPI.getCredits);

const STATUS: CreditStatus = {
  credits: null,
  metering_enabled: false,
  email_verification_required: false,
  is_email_verified: true,
  monthly_grant: null,
  balance_cap: null,
  next_grant_at: null,
  generation_costs: {},
};

const workspace = {
  id: '10',
  name: 'Algorithms',
  subjectArea: 'Computer Science',
  educationLevel: 'unspecified',
  semester: 'Fall',
  examDate: '',
  topics: ['Sorting'],
  syllabus: '',
  progress: null,
  updatedAt: '2026-08-20T10:00:00Z',
  accent: 'blue',
} as Workspace;

beforeEach(() => {
  vi.clearAllMocks();
  mockGetCredits.mockResolvedValue(STATUS);
  mockQuizList.mockResolvedValue([]);
  mockProgress.mockResolvedValue({
    status: 'practiced',
    attempts_count: 2,
    average_score: 0.5,
    topic_mastery: [
      {
        topic: 'Graph Algorithms',
        questions_answered: 4,
        questions_correct: 1,
        mastery_percentage: 25,
        status: 'Needs Review',
      },
    ],
    weak_topics: ['Graph Algorithms'],
    quiz_history: [],
  });
});

function renderPage() {
  return render(
    <MemoryRouter>
      <CreditProvider>
        <ProgressPage workspace={workspace} />
      </CreditProvider>
    </MemoryRouter>,
  );
}

describe('ProgressPage', () => {
  it('opens quiz generation on a weak topic, preset but still editable', async () => {
    renderPage();

    await userEvent.click(
      await screen.findByRole('button', { name: /Practice Graph Algorithms/ }),
    );

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toBeInTheDocument();
    expect(await screen.findByLabelText(/Which topic/)).toHaveValue('Graph Algorithms');
  });

  it('generates the quiz against the weak topic it was opened for', async () => {
    mockEnqueue.mockResolvedValue({ job_id: 10, status: 'queued' });

    renderPage();

    await userEvent.click(
      await screen.findByRole('button', { name: /Practice Graph Algorithms/ }),
    );
    await userEvent.click(await screen.findByRole('button', { name: /start the quiz/i }));

    await waitFor(() => expect(mockEnqueue).toHaveBeenCalled());
    expect(mockEnqueue.mock.calls[0][0]).toBe(10);
    expect(mockEnqueue.mock.calls[0][1]).toMatchObject({
      topic_focus: 'Graph Algorithms',
    });
  });
});

describe('ProgressPage headings', () => {
  it('names the screen with a single top-level heading', async () => {
    renderPage();

    const headings = await screen.findAllByRole('heading', { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent(/Algorithms/);
  });
});

describe('when progress cannot be read', () => {
  it('says so rather than showing a course nobody has studied', async () => {
    mockProgress.mockRejectedValue(new APIError(503, { detail: 'Progress is unavailable.' }));
    renderPage();

    expect(await screen.findByText('Progress is unavailable.')).toBeInTheDocument();
    expect(screen.queryByText(/no quiz activity yet/i)).toBeNull();
  });

  it('offers a way to try the read again', async () => {
    mockProgress.mockRejectedValueOnce(new APIError(503, { detail: 'Progress is unavailable.' }));
    renderPage();

    await screen.findByText('Progress is unavailable.');
    await userEvent.click(screen.getByRole('button', { name: /try again/i }));

    await waitFor(() => expect(mockProgress).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole('button', { name: /Practice Graph Algorithms/ })).toBeInTheDocument();
  });

  it('claims no score at all when the course has never been graded', async () => {
    mockProgress.mockResolvedValue({
      status: 'ready',
      attempts_count: 0,
      average_score: null,
      topic_mastery: [],
      weak_topics: [],
      quiz_history: [],
    });
    renderPage();

    await screen.findByRole('heading', { level: 1 });
    expect(screen.queryByText('0%')).toBeNull();
    expect(screen.queryByText('0')).toBeNull();
  });
});
