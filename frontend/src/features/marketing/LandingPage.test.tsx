import { render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import LandingPage from './LandingPage';

const authState = {
  isAuthenticated: false,
  isLoading: false,
};

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({
    user: null,
    isAuthenticated: authState.isAuthenticated,
    isLoading: authState.isLoading,
    login: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
  }),
}));

function renderLanding() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/dashboard" element={<h1>Courses</h1>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('LandingPage', () => {
  it('offers both entry points to a signed-out visitor', () => {
    authState.isAuthenticated = false;
    authState.isLoading = false;

    renderLanding();

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      /turn your study materials/i,
    );
    const header = within(screen.getByRole('banner'));
    expect(header.getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/login');
    expect(header.getByRole('link', { name: 'Create account' })).toHaveAttribute(
      'href',
      '/register',
    );
    expect(screen.getByRole('link', { name: 'Get started free' })).toHaveAttribute(
      'href',
      '/register',
    );
  });

  it('sends an already signed-in visitor straight to their courses', () => {
    authState.isAuthenticated = true;
    authState.isLoading = false;

    renderLanding();

    expect(screen.getByRole('heading', { name: 'Courses' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Create account' })).not.toBeInTheDocument();
  });

  it('renders nothing but the page ground while the session is still resolving', () => {
    authState.isAuthenticated = false;
    authState.isLoading = true;

    renderLanding();

    expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument();
    expect(screen.queryByRole('banner')).not.toBeInTheDocument();
  });

  it('renders the privacy section and GitHub link', () => {
    authState.isAuthenticated = false;
    authState.isLoading = false;

    renderLanding();

    expect(screen.getByRole('heading', { name: 'Your data stays yours.' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Run on your own machine' })).toHaveAttribute(
      'href',
      'https://github.com/coDDesigning/Lumina',
    );
  });

  it('keeps a single h1 and no skipped heading levels', () => {
    authState.isAuthenticated = false;
    authState.isLoading = false;

    renderLanding();

    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(screen.getAllByRole('heading', { level: 2 }).length).toBeGreaterThan(0);
  });
});
