import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { coursesAPI } from './api/courses';
import { progressAPI } from './api/progress';
import { createMockCourse } from './test/mocks/api';

vi.mock('./context/CreditContext', () => ({
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

vi.mock('./context/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 1,
      name: 'Test Student',
      email: 'student@example.com',
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

vi.mock('./api/courses', () => ({
  coursesAPI: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    listDocuments: vi.fn(),
    getDocumentStatus: vi.fn(),
    uploadDocument: vi.fn(),
    deleteDocument: vi.fn(),
    retryDocument: vi.fn(),
  },
}));

vi.mock('./api/progress', () => ({
  progressAPI: { get: vi.fn(), listAll: vi.fn() },
}));

const mockList = vi.mocked(coursesAPI.list);
const mockListProgress = vi.mocked(progressAPI.listAll);
const mockGetProgress = vi.mocked(progressAPI.get);

function renderCourses() {
  return render(
    <MemoryRouter initialEntries={['/courses']}>
      <App />
    </MemoryRouter>,
  );
}

describe('Course card progress loading', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockList.mockResolvedValue([
      createMockCourse({ id: 1, title: 'Operating Systems' }),
      createMockCourse({ id: 2, title: 'Algorithms' }),
    ]);
  });

  it('loads every course card from a single progress request', async () => {
    mockListProgress.mockResolvedValue([
      {
        course_id: 1,
        status: 'practiced',
        attempts_count: 2,
        average_score: 0.72,
        completion: 0.72,
        total_time_spent_seconds: 4320,
        last_activity: '2026-08-22T10:00:00Z',
      },
      {
        course_id: 2,
        status: 'ready',
        attempts_count: 0,
        average_score: null,
        completion: null,
        total_time_spent_seconds: null,
        last_activity: null,
      },
    ]);

    renderCourses();

    expect(await screen.findByText('72%')).toBeInTheDocument();
    await waitFor(() => expect(mockListProgress).toHaveBeenCalledTimes(1));
    expect(mockGetProgress).not.toHaveBeenCalled();
    expect(screen.getByText('No quiz activity yet')).toBeInTheDocument();
  });

  it('shows the status the backend sent rather than deriving one', async () => {
    mockListProgress.mockResolvedValue([
      {
        course_id: 1,
        status: 'processing',
        attempts_count: 0,
        average_score: null,
        completion: null,
        total_time_spent_seconds: null,
        last_activity: null,
      },
      {
        course_id: 2,
        status: 'mastered',
        attempts_count: 3,
        average_score: 0.91,
        completion: 0.91,
        total_time_spent_seconds: 600,
        last_activity: '2026-08-22T10:00:00Z',
      },
    ]);

    renderCourses();

    expect(await screen.findByText('Processing')).toBeInTheDocument();
    expect(screen.getByText('Sources still processing')).toBeInTheDocument();
    expect(screen.getByText('Mastered')).toBeInTheDocument();
    expect(screen.getByText('10m spent practising')).toBeInTheDocument();
  });

  it('reports time spent only for the courses that recorded it', async () => {
    mockListProgress.mockResolvedValue([
      {
        course_id: 1,
        status: 'practiced',
        attempts_count: 2,
        average_score: 0.72,
        completion: 0.72,
        total_time_spent_seconds: 4320,
        last_activity: '2026-08-22T10:00:00Z',
      },
      {
        course_id: 2,
        status: 'practiced',
        attempts_count: 1,
        average_score: 0.4,
        completion: 0.4,
        total_time_spent_seconds: null,
        last_activity: '2026-08-22T10:00:00Z',
      },
    ]);

    renderCourses();

    expect(await screen.findByText('1h 12m spent practising')).toBeInTheDocument();
    expect(screen.getAllByText(/spent practising/)).toHaveLength(1);
  });

  it('shows progress as unavailable when the progress request fails', async () => {
    mockListProgress.mockRejectedValue(new Error('network down'));

    renderCourses();

    expect(await screen.findByText('Operating Systems')).toBeInTheDocument();
    expect(screen.getAllByText('Progress unavailable')).toHaveLength(2);
    expect(screen.queryByText('No quiz activity yet')).not.toBeInTheDocument();
  });
});
