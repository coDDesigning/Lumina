import type { ReactElement } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import LoginPage from './LoginPage';
import RegisterPage from './RegisterPage';

const login = vi.fn();

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({
    user: null,
    isAuthenticated: false,
    isLoading: false,
    login,
    logout: vi.fn(),
    refreshUser: vi.fn(),
  }),
}));

vi.mock('@/api/auth', () => ({
  authAPI: {
    login: vi.fn(),
    register: vi.fn(),
    me: vi.fn(),
  },
}));

const { authAPI } = await import('@/api/auth');
const mockedLogin = vi.mocked(authAPI.login);
const mockedRegister = vi.mocked(authAPI.register);

function renderAt(
  element: ReactElement,
  initialEntry: string | { pathname: string; state?: unknown },
) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/login" element={element} />
        <Route path="/register" element={element} />
        <Route path="/dashboard" element={<h1>Courses</h1>} />
        <Route path="/courses/12" element={<h1>CS 3410</h1>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  login.mockReset();
  mockedLogin.mockReset();
  mockedRegister.mockReset();
});

describe('LoginPage', () => {
  it('labels both credentials fields', () => {
    renderAt(<LoginPage />, '/login');
    expect(screen.getByLabelText('Email')).toBeInTheDocument();
    expect(screen.getByLabelText('Password')).toBeInTheDocument();
  });

  it('signs in and lands on the dashboard', async () => {
    mockedLogin.mockResolvedValue({ access_token: 'token-abc', token_type: 'bearer' });

    renderAt(<LoginPage />, '/login');

    await userEvent.type(screen.getByLabelText('Email'), 'deniz@uni.edu');
    await userEvent.type(screen.getByLabelText('Password'), 'correct-horse');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    await waitFor(() => {
      expect(mockedLogin).toHaveBeenCalledWith('deniz@uni.edu', 'correct-horse');
    });
    expect(login).toHaveBeenCalledWith('token-abc');
    expect(await screen.findByRole('heading', { name: 'Courses' })).toBeInTheDocument();
  });

  it('returns the user to the page that sent them to sign in', async () => {
    mockedLogin.mockResolvedValue({ access_token: 'token-abc', token_type: 'bearer' });

    renderAt(<LoginPage />, {
      pathname: '/login',
      state: { from: { pathname: '/courses/12' } },
    });

    await userEvent.type(screen.getByLabelText('Email'), 'deniz@uni.edu');
    await userEvent.type(screen.getByLabelText('Password'), 'correct-horse');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    expect(await screen.findByRole('heading', { name: 'CS 3410' })).toBeInTheDocument();
  });

  it('announces a rejected sign-in and keeps the user on the form', async () => {
    mockedLogin.mockRejectedValue(
      new APIError(401, { detail: 'Incorrect email or password' }, null),
    );

    renderAt(<LoginPage />, '/login');

    await userEvent.type(screen.getByLabelText('Email'), 'deniz@uni.edu');
    await userEvent.type(screen.getByLabelText('Password'), 'wrong');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/incorrect email or password/i);
    expect(login).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Email')).toHaveValue('deniz@uni.edu');
  });
});

describe('RegisterPage', () => {
  it('refuses mismatched passwords without calling the API', async () => {
    renderAt(<RegisterPage />, '/register');

    await userEvent.type(screen.getByLabelText('Name'), 'Deniz Kaya');
    await userEvent.type(screen.getByLabelText('Email'), 'deniz@uni.edu');
    await userEvent.type(screen.getByLabelText('Password'), 'correct-horse');
    await userEvent.type(screen.getByLabelText('Confirm password'), 'correct-hoose');
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }));

    expect(await screen.findByText('Those two passwords do not match.')).toBeInTheDocument();
    expect(mockedRegister).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Confirm password')).toHaveAttribute('aria-invalid', 'true');
  });

  it('refuses a password over the 72-byte bcrypt ceiling', async () => {
    renderAt(<RegisterPage />, '/register');

    const tooLong = 'ü'.repeat(40);
    await userEvent.type(screen.getByLabelText('Name'), 'Deniz Kaya');
    await userEvent.type(screen.getByLabelText('Email'), 'deniz@uni.edu');
    await userEvent.type(screen.getByLabelText('Password'), tooLong);
    await userEvent.type(screen.getByLabelText('Confirm password'), tooLong);
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }));

    expect(
      await screen.findByText('That password is too long. Keep it under 72 bytes.'),
    ).toBeInTheDocument();
    expect(mockedRegister).not.toHaveBeenCalled();
  });

  it('registers, signs in, and lands on the dashboard', async () => {
    mockedRegister.mockResolvedValue({
      id: 1,
      name: 'Deniz Kaya',
      email: 'deniz@uni.edu',
      role: 'user',
      is_banned: false,
      credits: 20,
      preferred_model: 'gemini:gemini-3.6-flash',
      education_level: 'unspecified',
    });
    mockedLogin.mockResolvedValue({ access_token: 'token-xyz', token_type: 'bearer' });

    renderAt(<RegisterPage />, '/register');

    await userEvent.type(screen.getByLabelText('Name'), '  Deniz Kaya  ');
    await userEvent.type(screen.getByLabelText('Email'), '  deniz@uni.edu  ');
    await userEvent.type(screen.getByLabelText('Password'), 'correct-horse');
    await userEvent.type(screen.getByLabelText('Confirm password'), 'correct-horse');
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }));

    await waitFor(() => {
      expect(mockedRegister).toHaveBeenCalledWith('Deniz Kaya', 'deniz@uni.edu', 'correct-horse');
    });
    expect(mockedLogin).toHaveBeenCalledWith('deniz@uni.edu', 'correct-horse');
    expect(login).toHaveBeenCalledWith('token-xyz');
    expect(await screen.findByRole('heading', { name: 'Courses' })).toBeInTheDocument();
  });

  it('states the password rules before the user submits', () => {
    renderAt(<RegisterPage />, '/register');
    expect(screen.getByText('At least 8 characters, up to 72 bytes.')).toBeInTheDocument();
  });
});

describe('Auth pages landmarks', () => {
  it('puts the sign-in form in a main landmark', () => {
    renderAt(<LoginPage />, '/login');
    expect(screen.getByRole('main')).toBeInTheDocument();
  });

  it('puts the registration form in a main landmark', () => {
    renderAt(<RegisterPage />, '/register');
    expect(screen.getByRole('main')).toBeInTheDocument();
  });
});
