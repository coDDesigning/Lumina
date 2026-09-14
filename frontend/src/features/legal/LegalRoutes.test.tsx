import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '@/App';
import { legalAPI } from '@/api/legal';

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({
    user: null,
    isAuthenticated: false,
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
  }),
}));

vi.mock('@/api/legal', () => ({
  legalAPI: {
    getConfig: vi.fn(),
  },
}));

const mockedLegalConfig = vi.mocked(legalAPI.getConfig);

beforeEach(() => {
  mockedLegalConfig.mockResolvedValue({
    enabled: true,
    terms_version: '1.0',
    privacy_version: '1.0',
    effective_date: '2026-09-12',
  });
});

const routes = [
  ['/legal/privacy', 'Privacy Notice'],
  ['/legal/terms', 'Terms of Service'],
  ['/legal/acceptable-use', 'Acceptable Use Policy'],
  ['/legal/cookies', 'Cookie & Browser Storage Policy'],
  ['/legal/ai-disclosure', 'AI / Educational Disclosure'],
  ['/legal/security', 'Security & Responsible Disclosure'],
  ['/legal/open-source', 'Open Source & Third-Party Notices'],
] as const;

describe('public legal routes', () => {
  it.each(routes)('renders %s without an authenticated session', async (route, heading) => {
    render(
      <MemoryRouter initialEntries={[route]}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { level: 1, name: heading })).toBeVisible();
    expect(screen.getByText(/effective 12 september 2026/i)).toBeVisible();
    expect(screen.getByText(/version 1\.0/i)).toBeVisible();
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main');
  });

  it('exposes every legal destination in the footer', async () => {
    render(
      <MemoryRouter initialEntries={['/legal/privacy']}>
        <App />
      </MemoryRouter>,
    );

    const legal = within(await screen.findByRole('navigation', { name: 'Legal' }));
    expect(legal.getAllByRole('link')).toHaveLength(routes.length);
    expect(legal.getByRole('link', { name: 'Privacy' })).toHaveAttribute('href', '/legal/privacy');
    expect(legal.getByRole('link', { name: 'Open source' })).toHaveAttribute('href', '/legal/open-source');
  });

  it('redirects legal routes home and hides the footer when the package is disabled', async () => {
    mockedLegalConfig.mockResolvedValue({
      enabled: false,
      terms_version: null,
      privacy_version: null,
      effective_date: null,
    });

    render(
      <MemoryRouter initialEntries={['/legal/privacy']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('link', { name: 'GitHub' })).toBeVisible();
    expect(screen.queryByRole('heading', { level: 1, name: 'Privacy Notice' })).toBeNull();
    expect(screen.queryByRole('navigation', { name: 'Legal' })).toBeNull();
  });
});
