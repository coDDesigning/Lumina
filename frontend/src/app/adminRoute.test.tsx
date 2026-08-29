import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { adminAPI } from '@/api/admin';
import { adsAPI } from '@/api/ads';
import { authAPI } from '@/api/auth';
import { coursesAPI } from '@/api/courses';
import { progressAPI } from '@/api/progress';
import type { AiCostReport, User } from '@/api/types';
import App from '@/App';

const ADMIN: User = {
  id: 1,
  name: 'Root',
  email: 'admin@example.com',
  role: 'admin',
  is_banned: false,
  is_email_verified: true,
  credits: null,
  preferred_model: 'gemini:gemini-3.6-flash',
  education_level: 'unspecified',
};

const LEARNER: User = {
  ...ADMIN,
  id: 2,
  name: 'Learner',
  email: 'learner@example.com',
  role: 'user',
  credits: 10,
};

const EMPTY_COST_REPORT: AiCostReport = {
  timezone: 'UTC',
  start_date: '2026-08-01',
  end_date: '2026-08-30',
  totals: {
    successful_generations: 0,
    prompt_tokens: 0,
    completion_tokens: 0,
    estimated_cost_usd: 0,
    unpriced_generations: 0,
  },
  daily: [],
};

const session: { user: User | null; isLoading: boolean } = {
  user: null,
  isLoading: false,
};
const login = vi.fn(async () => {
  session.user = ADMIN;
  session.isLoading = false;
});

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({
    user: session.user,
    isAuthenticated: session.user !== null,
    isLoading: session.isLoading,
    login,
    logout: vi.fn(),
    refreshUser: vi.fn(),
  }),
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

vi.mock('@/api/admin', () => ({
  adminAPI: {
    listUsers: vi.fn(),
    getAiCostReport: vi.fn(),
    banUser: vi.fn(),
    changeUserRole: vi.fn(),
    changeCredits: vi.fn(),
    listUserCreditTransactions: vi.fn(),
    listUserCourses: vi.fn(),
  },
}));

vi.mock('@/api/ads', () => ({
  adsAPI: {
    getConfig: vi.fn(),
  },
}));

vi.mock('@/api/auth', () => ({
  authAPI: {
    login: vi.fn(),
  },
}));

vi.mock('@/api/courses', () => ({
  coursesAPI: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('@/api/progress', () => ({
  progressAPI: {
    listAll: vi.fn(),
  },
}));

const mockedAdmin = vi.mocked(adminAPI);

function renderAdminRoute() {
  return render(
    <MemoryRouter initialEntries={['/admin']}>
      <App />
    </MemoryRouter>,
  );
}

function expectNoAdminRequests() {
  expect(mockedAdmin.listUsers).not.toHaveBeenCalled();
  expect(mockedAdmin.getAiCostReport).not.toHaveBeenCalled();
  expect(mockedAdmin.banUser).not.toHaveBeenCalled();
  expect(mockedAdmin.changeUserRole).not.toHaveBeenCalled();
  expect(mockedAdmin.changeCredits).not.toHaveBeenCalled();
  expect(mockedAdmin.listUserCreditTransactions).not.toHaveBeenCalled();
  expect(mockedAdmin.listUserCourses).not.toHaveBeenCalled();
}

beforeEach(() => {
  session.user = null;
  session.isLoading = false;
  login.mockClear();

  mockedAdmin.listUsers.mockResolvedValue([ADMIN]);
  mockedAdmin.getAiCostReport.mockResolvedValue(EMPTY_COST_REPORT);
  vi.mocked(adsAPI.getConfig).mockResolvedValue({
    enabled: false,
    provider: null,
    publisher_id: null,
  });
  vi.mocked(authAPI.login).mockResolvedValue({
    access_token: 'admin-token',
    token_type: 'bearer',
  });
  vi.mocked(coursesAPI.list).mockResolvedValue([]);
  vi.mocked(progressAPI.listAll).mockResolvedValue([]);
});

describe('admin route access', () => {
  it('renders the administrative page for an administrator', async () => {
    session.user = ADMIN;

    renderAdminRoute();

    expect(await screen.findByRole('heading', { name: 'Accounts' })).toBeInTheDocument();
    expect(mockedAdmin.listUsers).toHaveBeenCalledWith(
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(mockedAdmin.getAiCostReport).toHaveBeenCalledWith(
      30,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it('redirects a learner without issuing an administrative request', async () => {
    session.user = LEARNER;

    renderAdminRoute();

    expect(await screen.findByRole('heading', { name: 'Your courses' })).toBeInTheDocument();
    expect(screen.getByText('Administrator access required')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Accounts' })).not.toBeInTheDocument();
    expectNoAdminRequests();
  });

  it('does not reveal or query the administrative page while the session resolves', async () => {
    session.isLoading = true;
    const view = renderAdminRoute();

    expect(screen.getByRole('status')).toHaveTextContent('Checking your session');
    expect(screen.queryByRole('heading', { name: 'Accounts' })).not.toBeInTheDocument();
    expectNoAdminRequests();

    session.user = ADMIN;
    session.isLoading = false;
    view.rerender(
      <MemoryRouter initialEntries={['/admin']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: 'Accounts' })).toBeInTheDocument();
  });

  it('returns an administrator to /admin after sign-in', async () => {
    renderAdminRoute();

    expect(await screen.findByRole('heading', { name: 'Welcome back.' })).toBeInTheDocument();
    expectNoAdminRequests();

    await userEvent.type(screen.getByLabelText('Email'), 'admin@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'correct-horse');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    await waitFor(() => {
      expect(authAPI.login).toHaveBeenCalledWith('admin@example.com', 'correct-horse');
    });
    expect(login).toHaveBeenCalledWith('admin-token');
    expect(await screen.findByRole('heading', { name: 'Accounts' })).toBeInTheDocument();
  });
});
