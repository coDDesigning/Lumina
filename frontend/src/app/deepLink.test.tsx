import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { APIError } from '../api/client';
import { coursesAPI } from '../api/courses';
import { progressAPI } from '../api/progress';
import { createMockCourse } from '../test/mocks/api';

const session = { isAuthenticated: false, isLoading: true };

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({
    user: session.isAuthenticated
      ? { id: 1, name: 'Bora', email: 'b@example.com', role: 'student', is_banned: false }
      : null,
    isAuthenticated: session.isAuthenticated,
    isLoading: session.isLoading,
    login: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
}));

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
}));

vi.mock('../api/courses', () => ({
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

vi.mock('../api/progress', () => ({
  progressAPI: { get: vi.fn(), listAll: vi.fn() },
}));

vi.mock('../api/generatedOutputs', () => ({
  generatedOutputsAPI: { list: vi.fn().mockResolvedValue([]), get: vi.fn() },
}));

vi.mock('../api/conversations', () => ({
  conversationsAPI: {
    list: vi.fn().mockResolvedValue([]),
    get: vi.fn(),
    delete: vi.fn(),
  },
}));

beforeEach(() => {
  vi.mocked(coursesAPI.list).mockResolvedValue([
    createMockCourse({ id: 1, title: 'Operating Systems' }),
  ]);
  vi.mocked(coursesAPI.listDocuments).mockResolvedValue([]);
  vi.mocked(progressAPI.get).mockResolvedValue({
    status: 'no_documents',
    attempts_count: 0,
    average_score: null,
    topic_mastery: [],
  });
  vi.mocked(progressAPI.listAll).mockResolvedValue([]);
});

describe('opening a course link directly', () => {
  it('does not decide the course is missing while the session is still resolving', async () => {
    session.isAuthenticated = false;
    session.isLoading = true;

    const view = render(
      <MemoryRouter initialEntries={['/courses/1']}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByRole('status')).toBeInTheDocument();

    session.isAuthenticated = true;
    session.isLoading = false;
    view.rerender(
      <MemoryRouter initialEntries={['/courses/1']}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add Sources' })).toBeInTheDocument();
    });
    expect(screen.queryByRole('heading', { name: 'Your courses' })).toBeNull();
  });

  it('lands on the course once the session is known', async () => {
    session.isAuthenticated = true;
    session.isLoading = false;

    render(
      <MemoryRouter initialEntries={['/courses/1']}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add Sources' })).toBeInTheDocument();
    });
    expect(screen.queryByRole('heading', { name: 'Your courses' })).toBeNull();
  });
});

describe('opening a course link the course list cannot answer', () => {
  beforeEach(() => {
    session.isAuthenticated = true;
    session.isLoading = false;
    vi.mocked(coursesAPI.get).mockResolvedValue(
      createMockCourse({ id: 7, title: 'Distributed Systems' }),
    );
  });

  it('resolves while the course list is still in flight', async () => {
    // The list never settles. A cold link has its own read and must not wait
    // on a request that answers a different question.
    vi.mocked(coursesAPI.list).mockReturnValue(new Promise(() => {}));

    render(
      <MemoryRouter initialEntries={['/courses/7']}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add Sources' })).toBeInTheDocument();
    });
    expect(vi.mocked(coursesAPI.get)).toHaveBeenCalledWith(7, expect.anything());
  });

  it('resolves when the course list fails outright', async () => {
    vi.mocked(coursesAPI.list).mockRejectedValue(new TypeError('Failed to fetch'));

    render(
      <MemoryRouter initialEntries={['/courses/7']}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add Sources' })).toBeInTheDocument();
    });
  });
});

describe('a course link that cannot be resolved', () => {
  beforeEach(() => {
    session.isAuthenticated = true;
    session.isLoading = false;
    vi.mocked(coursesAPI.list).mockResolvedValue([]);
  });

  it('offers a retry rather than a redirect when the network is down', async () => {
    // Offline is not "this course does not exist". Sending the reader to the
    // dashboard with nothing said is what this replaces.
    vi.mocked(coursesAPI.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(
      <MemoryRouter initialEntries={['/courses/7']}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
    });
    expect(screen.getByText('This course could not be loaded')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Network error');
    expect(screen.queryByRole('heading', { name: 'Your courses' })).toBeNull();
  });

  it('recovers when the retry succeeds', async () => {
    vi.mocked(coursesAPI.get).mockRejectedValueOnce(new TypeError('Failed to fetch'));

    render(
      <MemoryRouter initialEntries={['/courses/7']}>
        <App />
      </MemoryRouter>,
    );

    const retry = await screen.findByRole('button', { name: 'Try again' });
    vi.mocked(coursesAPI.get).mockResolvedValue(
      createMockCourse({ id: 7, title: 'Distributed Systems' }),
    );
    retry.click();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add Sources' })).toBeInTheDocument();
    });
  });

  it('says the course is not available when the server says it is gone', async () => {
    vi.mocked(coursesAPI.get).mockRejectedValue(
      new APIError(404, { detail: 'Course not found' }),
    );

    render(
      <MemoryRouter initialEntries={['/courses/7']}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: 'This course is not available' }),
      ).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });

  it('does not fetch at all for an identifier that is not a course id', async () => {
    render(
      <MemoryRouter initialEntries={['/courses/not-a-number']}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: 'This course is not available' }),
      ).toBeInTheDocument();
    });
    expect(vi.mocked(coursesAPI.get)).not.toHaveBeenCalled();
  });
});
