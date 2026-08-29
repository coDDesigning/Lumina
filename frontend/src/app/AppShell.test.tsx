import { render, screen, within } from '@testing-library/react';
import type { RenderOptions } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppShell } from './AppShell';
import { ThemeProvider } from './ThemeProvider';
import { THEME_STORAGE_KEY } from './themeContext';

const logout = vi.fn();
const authState = { role: 'user' as 'user' | 'admin' };
const routeError = new Error('Route render failed');

function ThrowingRoute(): never {
  throw routeError;
}

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 1,
      name: 'Deniz Kaya',
      email: 'deniz@uni.edu',
      role: authState.role,
      is_banned: false,
      is_email_verified: true,
      credits: null,
      preferred_model: 'ollama:llama3.1',
      education_level: 'unspecified',
    },
    isAuthenticated: true,
    isLoading: false,
    login: vi.fn(),
    logout,
    refreshUser: vi.fn(),
  }),
}));

function renderShell(initialPath = '/dashboard', options?: RenderOptions) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/dashboard" element={<h1>Courses</h1>} />
            <Route path="/account" element={<h1>Account</h1>} />
            <Route path="/broken" element={<ThrowingRoute />} />
          </Route>
          <Route path="/login" element={<h1>Sign in</h1>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
    options,
  );
}

beforeEach(() => {
  logout.mockReset();
  authState.role = 'user';
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
});

afterEach(() => {
  document.documentElement.removeAttribute('data-theme');
});

describe('AppShell', () => {
  it('renders the routed page inside a main landmark', () => {
    renderShell();
    expect(within(screen.getByRole('main')).getByRole('heading', { name: 'Courses' })).toBeInTheDocument();
  });

  it('contains route render failures and resets the boundary on navigation', async () => {
    const user = userEvent.setup();
    const onCaughtError = vi.fn();
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});

    renderShell('/broken', { onCaughtError });

    const main = screen.getByRole('main');
    expect(within(main).getByRole('heading', { name: 'Something in the page broke.' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument();

    expect(onCaughtError).toHaveBeenCalledOnce();
    expect(onCaughtError).toHaveBeenCalledWith(
      routeError,
      expect.objectContaining({ componentStack: expect.any(String) }),
    );
    expect(consoleError).toHaveBeenCalledOnce();
    expect(consoleError).toHaveBeenCalledWith(
      'Unhandled interface error',
      routeError,
      expect.stringContaining('ThrowingRoute'),
    );

    const nav = within(screen.getByRole('navigation', { name: 'Main' }));
    await user.click(nav.getByRole('link', { name: 'Courses' }));

    expect(await within(main).findByRole('heading', { name: 'Courses' })).toBeInTheDocument();
    expect(within(main).queryByRole('heading', { name: 'Something in the page broke.' })).not.toBeInTheDocument();
    consoleError.mockRestore();
  });

  it('exposes navigation as a labelled landmark with accessible names', () => {
    renderShell();

    const nav = within(screen.getByRole('navigation', { name: 'Main' }));
    expect(nav.getByRole('link', { name: 'Courses' })).toHaveAttribute('href', '/dashboard');
    expect(nav.getByRole('link', { name: 'Account' })).toHaveAttribute('href', '/account');
  });

  it('hides the admin destination from a student', () => {
    renderShell();
    expect(screen.queryByRole('link', { name: 'Admin' })).not.toBeInTheDocument();
  });

  it('offers the admin destination to an administrator', () => {
    authState.role = 'admin';
    renderShell();
    expect(screen.getByRole('link', { name: 'Admin' })).toHaveAttribute('href', '/admin');
  });

  it('toggles the theme and remembers the choice', async () => {
    renderShell();

    await userEvent.click(screen.getByRole('button', { name: 'Switch to dark theme' }));

    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');

    await userEvent.click(screen.getByRole('button', { name: 'Switch to light theme' }));

    expect(document.documentElement).toHaveAttribute('data-theme', 'light');
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light');
  });

  it('signs the user out and sends them to the sign-in page', async () => {
    renderShell();

    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }));

    expect(logout).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  });

  it('marks the destination matching the current route as current', () => {
    renderShell('/account');

    const nav = within(screen.getByRole('navigation', { name: 'Main' }));
    expect(nav.getByRole('link', { name: 'Account' })).toHaveAttribute('aria-current', 'page');
    expect(nav.getByRole('link', { name: 'Courses' })).not.toHaveAttribute('aria-current');
  });
});
