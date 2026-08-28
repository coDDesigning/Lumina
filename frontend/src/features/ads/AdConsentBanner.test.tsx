import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { adsAPI } from '@/api/ads';
import { queryCache } from '@/lib/query/cache';
import { AdConsentBanner } from './AdConsentBanner';
import { AD_CONSENT_STORAGE_KEY } from './useAdConsent';

vi.mock('@/api/ads', () => ({
  adsAPI: {
    getConfig: vi.fn(),
    recordTelemetry: vi.fn(),
  },
}));

const mockGetConfig = vi.mocked(adsAPI.getConfig);

describe('AdConsentBanner component', () => {
  beforeEach(() => {
    localStorage.clear();
    queryCache.clear();
    vi.clearAllMocks();
  });

  it('renders nothing when hosted ads are disabled (self-hosted mode)', async () => {
    mockGetConfig.mockResolvedValue({
      enabled: false,
      provider: null,
      publisher_id: null,
    });

    render(<AdConsentBanner />);

    expect(
      screen.queryByRole('region', { name: 'Advertising and privacy choices' }),
    ).not.toBeInTheDocument();
  });

  it('renders banner when hosted ads are enabled and consent is pending', async () => {
    mockGetConfig.mockResolvedValue({
      enabled: true,
      provider: 'ethicalads',
      publisher_id: 'lumina-test',
    });

    render(<AdConsentBanner />);

    expect(
      await screen.findByRole('region', { name: 'Advertising and privacy choices' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Support Lumina with privacy-first ads' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Allow' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Decline' })).toBeInTheDocument();
  });

  it('grants consent when user clicks Allow and dismisses the banner', async () => {
    const user = userEvent.setup();
    mockGetConfig.mockResolvedValue({
      enabled: true,
      provider: 'ethicalads',
      publisher_id: 'lumina-test',
    });

    render(<AdConsentBanner />);

    const allowBtn = await screen.findByRole('button', { name: 'Allow' });
    await user.click(allowBtn);

    expect(localStorage.getItem(AD_CONSENT_STORAGE_KEY)).toBe('granted');
    expect(
      screen.queryByRole('region', { name: 'Advertising and privacy choices' }),
    ).not.toBeInTheDocument();
  });

  it('denies consent when user clicks Decline and dismisses the banner', async () => {
    const user = userEvent.setup();
    mockGetConfig.mockResolvedValue({
      enabled: true,
      provider: 'ethicalads',
      publisher_id: 'lumina-test',
    });

    render(<AdConsentBanner />);

    const declineBtn = await screen.findByRole('button', { name: 'Decline' });
    await user.click(declineBtn);

    expect(localStorage.getItem(AD_CONSENT_STORAGE_KEY)).toBe('denied');
    expect(
      screen.queryByRole('region', { name: 'Advertising and privacy choices' }),
    ).not.toBeInTheDocument();
  });
});
