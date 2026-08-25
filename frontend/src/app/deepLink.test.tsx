import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
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
