import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { authAPI } from '../api/auth';
import { AuthProvider, useAuth } from './AuthContext';
import { createMockUser, MockErrors } from '../test/mocks/api';

vi.mock('../api/auth', () => ({
  authAPI: {
    me: vi.fn(),
    login: vi.fn(),
    register: vi.fn(),
  },
}));

const mockMe = vi.mocked(authAPI.me);

describe('AuthContext', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('throws error when useAuth is called outside AuthProvider', () => {
    expect(() => renderHook(() => useAuth())).toThrow(
      'useAuth must be used within an AuthProvider',
    );
  });

  it('initializes as unauthenticated when no token is in localStorage', async () => {
    const { result } = renderHook(() => useAuth(), {
      wrapper: ({ children }) => <AuthProvider>{children}</AuthProvider>,
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.user).toBeNull();
    expect(mockMe).not.toHaveBeenCalled();
  });

  it('fetches user and authenticates on mount when token exists', async () => {
    localStorage.setItem('token', 'valid-token-xyz');
    const mockUser = createMockUser({ name: 'Alice Smith' });
    mockMe.mockResolvedValueOnce(mockUser);

    const { result } = renderHook(() => useAuth(), {
      wrapper: ({ children }) => <AuthProvider>{children}</AuthProvider>,
    });

    expect(result.current.isLoading).toBe(true);

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(mockMe).toHaveBeenCalledTimes(1);
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.user).toEqual(mockUser);
  });

  it('clears token and resets user when token verification fails on mount', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    localStorage.setItem('token', 'expired-token');
    mockMe.mockRejectedValueOnce(MockErrors.unauthorized('Token expired'));

    const { result } = renderHook(() => useAuth(), {
      wrapper: ({ children }) => <AuthProvider>{children}</AuthProvider>,
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(localStorage.getItem('token')).toBeNull();
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.user).toBeNull();
    consoleSpy.mockRestore();
  });

  it('logs in successfully, saves token, and loads user profile', async () => {
    const mockUser = createMockUser({ name: 'Bob Jones' });
    mockMe.mockResolvedValue(mockUser);

    const { result } = renderHook(() => useAuth(), {
      wrapper: ({ children }) => <AuthProvider>{children}</AuthProvider>,
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    await act(async () => {
      await result.current.login('fresh-jwt-token');
    });

    expect(localStorage.getItem('token')).toBe('fresh-jwt-token');
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.user).toEqual(mockUser);
  });

  it('logs out, clears token from localStorage, and clears user state', async () => {
    localStorage.setItem('token', 'active-token');
    mockMe.mockResolvedValueOnce(createMockUser());

    const { result } = renderHook(() => useAuth(), {
      wrapper: ({ children }) => <AuthProvider>{children}</AuthProvider>,
    });

    await waitFor(() => {
      expect(result.current.isAuthenticated).toBe(true);
    });

    act(() => {
      result.current.logout();
    });

    expect(localStorage.getItem('token')).toBeNull();
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.user).toBeNull();
  });

  it('handles auth:unauthorized window event by clearing user', async () => {
    localStorage.setItem('token', 'some-token');
    mockMe.mockResolvedValueOnce(createMockUser());

    function TestConsumer() {
      const { user, isAuthenticated } = useAuth();
      return (
        <div>
          <span data-testid="auth-state">{isAuthenticated ? 'logged-in' : 'logged-out'}</span>
          <span data-testid="user-name">{user?.name ?? 'no-user'}</span>
        </div>
      );
    }

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('auth-state')).toHaveTextContent('logged-in');
    });

    act(() => {
      window.dispatchEvent(new CustomEvent('auth:unauthorized'));
    });

    expect(screen.getByTestId('auth-state')).toHaveTextContent('logged-out');
    expect(screen.getByTestId('user-name')).toHaveTextContent('no-user');
  });
});
