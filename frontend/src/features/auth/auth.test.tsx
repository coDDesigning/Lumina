import type { ReactElement } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import LoginPage from './LoginPage';
import RegisterPage from './RegisterPage';
import VerifyEmailPage from './VerifyEmailPage';

const login = vi.fn();
const refreshUser = vi.fn();
const refreshCredits = vi.fn();

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({
    user: null,
    isAuthenticated: false,
    isLoading: false,
    login,
    logout: vi.fn(),
    refreshUser,
  }),
}));

vi.mock('@/context/CreditContext', () => ({
  useCredits: () => ({
    status: null,
    isLoading: false,
    error: null,
    refresh: refreshCredits,
    isMetered: false,
    costOf: () => null,
    canAfford: () => true,
  }),
}));

vi.mock('@/api/auth', () => ({
  authAPI: {
    login: vi.fn(),
    register: vi.fn(),
    verifyEmail: vi.fn(),
    resendVerification: vi.fn(),
    me: vi.fn(),
  },
}));

const { authAPI } = await import('@/api/auth');
const mockedLogin = vi.mocked(authAPI.login);
const mockedRegister = vi.mocked(authAPI.register);
const mockedVerifyEmail = vi.mocked(authAPI.verifyEmail);
const mockedResend = vi.mocked(authAPI.resendVerification);

function renderAt(
  element: ReactElement,
  initialEntry: string | { pathname: string; state?: unknown },
) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/login" element={element} />
        <Route path="/register" element={element} />
        <Route path="/verify-email" element={element} />
        <Route path="/dashboard" element={<h1>Courses</h1>} />
        <Route path="/courses/12" element={<h1>CS 3410</h1>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  login.mockReset();
  refreshUser.mockReset();
  refreshCredits.mockReset();
  mockedLogin.mockReset();
  mockedRegister.mockReset();
  mockedVerifyEmail.mockReset();
  mockedResend.mockReset();
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
      await screen.findByText('That password is too long.'),
    ).toBeInTheDocument();
    expect(mockedRegister).not.toHaveBeenCalled();
  });

  it('registers, signs in, and lands on the dashboard', async () => {
    mockedRegister.mockResolvedValue({
      message: 'User registered successfully',
      user_email: 'deniz@uni.edu',
      role: 'user',
      // A deployment that does not verify addresses sends the user straight on.
      email_verification_required: false,
      is_email_verified: false,
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
    expect(
      screen.getByText(/At least 8 characters\./),
    ).toBeInTheDocument();
  });

  it('holds a hosted registration at the inbox instead of the dashboard', async () => {
    mockedRegister.mockResolvedValue({
      message: 'Check your email to confirm your address.',
      user_email: 'deniz@uni.edu',
      role: 'user',
      email_verification_required: true,
      is_email_verified: false,
    });
    mockedLogin.mockResolvedValue({ access_token: 'token-xyz', token_type: 'bearer' });

    renderAt(<RegisterPage />, '/register');

    await userEvent.type(screen.getByLabelText('Name'), 'Deniz Kaya');
    await userEvent.type(screen.getByLabelText('Email'), 'deniz@uni.edu');
    await userEvent.type(screen.getByLabelText('Password'), 'correct-horse');
    await userEvent.type(screen.getByLabelText('Confirm password'), 'correct-horse');
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }));

    expect(
      await screen.findByText('Check your email to confirm your address.'),
    ).toBeInTheDocument();
    expect(screen.getByText(/We sent a confirmation link to deniz@uni.edu/)).toBeInTheDocument();
    // The account exists and the session is real; only the credits are held back.
    expect(login).toHaveBeenCalledWith('token-xyz');
    expect(screen.queryByRole('heading', { name: 'Courses' })).not.toBeInTheDocument();
    // No bypass: the only way on is the emailed link.
    expect(screen.queryByRole('link', { name: /skip|look around/i })).not.toBeInTheDocument();
  });

  it('asks for another link without making the user retype their address', async () => {
    mockedRegister.mockResolvedValue({
      message: 'Check your email to confirm your address.',
      user_email: 'deniz@uni.edu',
      role: 'user',
      email_verification_required: true,
      is_email_verified: false,
    });
    mockedLogin.mockResolvedValue({ access_token: 'token-xyz', token_type: 'bearer' });
    mockedResend.mockResolvedValue({
      message: 'A new link is on its way.',
      is_email_verified: false,
      credits_granted: null,
    });

    renderAt(<RegisterPage />, '/register');

    await userEvent.type(screen.getByLabelText('Name'), 'Deniz Kaya');
    await userEvent.type(screen.getByLabelText('Email'), 'deniz@uni.edu');
    await userEvent.type(screen.getByLabelText('Password'), 'correct-horse');
    await userEvent.type(screen.getByLabelText('Confirm password'), 'correct-horse');
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }));

    await userEvent.click(await screen.findByRole('button', { name: 'Send the link again' }));

    await waitFor(() => {
      expect(mockedResend).toHaveBeenCalledWith('deniz@uni.edu');
    });
    expect(await screen.findByText('A new link is on its way.')).toBeInTheDocument();
  });
});

describe('VerifyEmailPage', () => {
  it('redeems the token once and names the credits it released', async () => {
    mockedVerifyEmail.mockResolvedValue({
      message: 'Your email address is confirmed.',
      is_email_verified: true,
      credits_granted: 20,
    });

    renderAt(<VerifyEmailPage />, '/verify-email?token=link-token');

    expect(
      await screen.findByText(/Your email address is confirmed\./),
    ).toBeInTheDocument();
    expect(screen.getByText(/Your 20 starting credits are in your account\./)).toBeInTheDocument();
    expect(mockedVerifyEmail).toHaveBeenCalledTimes(1);
    expect(mockedVerifyEmail).toHaveBeenCalledWith('link-token');
    // Verifying is the moment the balance changes, so both snapshots are stale.
    await waitFor(() => {
      expect(refreshUser).toHaveBeenCalled();
    });
    expect(refreshCredits).toHaveBeenCalled();
  });

  it('offers a new link when the token is spent or expired', async () => {
    mockedVerifyEmail.mockRejectedValue(
      new APIError(400, { detail: 'This verification link is no longer valid.' }, null),
    );

    renderAt(<VerifyEmailPage />, '/verify-email?token=stale-token');

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/no longer valid/i);
    expect(screen.getByRole('button', { name: 'Send a new link' })).toBeInTheDocument();
  });

  it('spends nothing when the link arrives without a token', async () => {
    renderAt(<VerifyEmailPage />, '/verify-email');

    expect(await screen.findByText(/missing its token/i)).toBeInTheDocument();
    expect(mockedVerifyEmail).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Email')).toBeInTheDocument();
  });

  it('sends a fresh link to a typed address', async () => {
    mockedResend.mockResolvedValue({
      message: 'A new link is on its way.',
      is_email_verified: false,
      credits_granted: null,
    });

    renderAt(<VerifyEmailPage />, '/verify-email');

    await userEvent.type(screen.getByLabelText('Email'), '  deniz@uni.edu  ');
    await userEvent.click(screen.getByRole('button', { name: 'Send a new link' }));

    await waitFor(() => {
      expect(mockedResend).toHaveBeenCalledWith('deniz@uni.edu');
    });
    expect(await screen.findByText('A new link is on its way.')).toBeInTheDocument();
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
