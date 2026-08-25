import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { queryKeys } from '@/api/queryKeys';
import { queryCache } from '@/lib/query/cache';
import { AuthProvider, useAuth } from './AuthContext';

vi.mock('@/api/auth', () => ({
  authAPI: {
    me: vi.fn(),
    login: vi.fn(),
    register: vi.fn(),
  },
}));

const { authAPI } = await import('@/api/auth');
const mockMe = vi.mocked(authAPI.me);

function Controls() {
  const { user, logout, login } = useAuth();
  return (
    <div>
      <p data-testid="who">{user?.email ?? 'nobody'}</p>
      <button type="button" onClick={logout}>
        Sign out
      </button>
      <button type="button" onClick={() => void login('second-token')}>
        Sign in again
      </button>
    </div>
  );
}

function seedCourseData() {
  queryCache.subscribe(
    queryKeys.courseDocuments(3),
    {
      fetcher: () => Promise.resolve(['secret.pdf']),
      fallbackMessage: 'Sources could not be loaded.',
      staleTime: 60_000,
      gcTime: 60_000,
      refetchOnFocus: false,
      onRefetchError: 'keep',
    },
    () => {},
  );
}

describe('cached data and identity', () => {
  beforeEach(() => {
    localStorage.clear();
    mockMe.mockReset();
  });

  it('drops every cached course read when the user signs out', async () => {
    localStorage.setItem('token', 'first-token');
    mockMe.mockResolvedValue({ id: 1, email: 'first@example.com' } as never);
    render(
      <AuthProvider>
        <Controls />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId('who')).toHaveTextContent('first@example.com'));

    seedCourseData();
    await waitFor(() =>
      expect(queryCache.getState(queryKeys.courseDocuments(3)).data).toEqual(['secret.pdf']),
    );

    screen.getByRole('button', { name: 'Sign out' }).click();

    await waitFor(() =>
      expect(queryCache.getState(queryKeys.courseDocuments(3)).data).toBeUndefined(),
    );
  });

  it('drops cached reads when the API reports the session is gone', async () => {
    localStorage.setItem('token', 'first-token');
    mockMe.mockResolvedValue({ id: 1, email: 'first@example.com' } as never);
    render(
      <AuthProvider>
        <Controls />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId('who')).toHaveTextContent('first@example.com'));

    seedCourseData();
    await waitFor(() =>
      expect(queryCache.getState(queryKeys.courseDocuments(3)).data).toEqual(['secret.pdf']),
    );

    window.dispatchEvent(new Event('auth:unauthorized'));

    await waitFor(() =>
      expect(queryCache.getState(queryKeys.courseDocuments(3)).data).toBeUndefined(),
    );
  });
});
