import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '@/App';
import { coursesAPI } from '@/api/courses';
import { progressAPI } from '@/api/progress';
import { createMockCourse } from '@/test/mocks/api';

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
      name: 'Deniz Kaya',
      email: 'deniz@uni.edu',
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

vi.mock('@/api/courses', () => ({
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

vi.mock('@/api/progress', () => ({
  progressAPI: { get: vi.fn(), listAll: vi.fn() },
}));

const mockList = vi.mocked(coursesAPI.list);
const mockListDocuments = vi.mocked(coursesAPI.listDocuments);
const mockGetProgress = vi.mocked(progressAPI.get);

function renderAppAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockList.mockResolvedValue([createMockCourse({ id: 1, title: 'Operating Systems' })]);
  mockListDocuments.mockResolvedValue([]);
  mockGetProgress.mockResolvedValue({
    status: 'no_documents',
    attempts_count: 0,
    average_score: null,
    topic_mastery: [],
  });
  vi.mocked(progressAPI.listAll).mockResolvedValue([]);
});

describe('links that were saved before the rename', () => {
  it('takes an old course link to the same course', async () => {
    renderAppAt('/workspaces/1');

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add Sources' })).toBeInTheDocument();
    });
  });

  it('takes an old course sub-page link to the same sub-page', async () => {
    renderAppAt('/workspaces/1/progress');

    await waitFor(() => {
      expect(screen.getByText('Progress')).toBeInTheDocument();
    });
  });

  it('takes an old profile link to the account page', async () => {
    renderAppAt('/profile');

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 1, name: 'Account' })).toBeInTheDocument();
    });
  });
});

describe('shell landmarks', () => {
  it('exposes exactly one main landmark on the course list', async () => {
    renderAppAt('/dashboard');

    await waitFor(() => {
      expect(screen.getByRole('navigation', { name: 'Main' })).toBeInTheDocument();
    });

    expect(screen.getAllByRole('main')).toHaveLength(1);
  });

  it('exposes exactly one main landmark inside a course workspace', async () => {
    renderAppAt('/courses/1');

    await waitFor(() => {
      expect(screen.getByRole('navigation', { name: 'Main' })).toBeInTheDocument();
    });

    expect(screen.getAllByRole('main')).toHaveLength(1);
  });

  it('offers a skip link that reaches the main landmark before the rail', async () => {
    renderAppAt('/dashboard');

    await waitFor(() => {
      expect(screen.getByRole('navigation', { name: 'Main' })).toBeInTheDocument();
    });

    const skip = screen.getByRole('link', { name: 'Skip to content' });
    expect(skip).toHaveAttribute('href', '#main');

    const main = screen.getByRole('main');
    expect(main).toHaveAttribute('id', 'main');
    expect(main).toHaveAttribute('tabindex', '-1');

    expect(skip.compareDocumentPosition(main) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const rail = screen.getByRole('navigation', { name: 'Main' });
    expect(skip.compareDocumentPosition(rail) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('renders no application shell on the unauthenticated sign-in route', () => {
    renderAppAt('/login');

    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Welcome back.' })).toBeInTheDocument();
  });
});
