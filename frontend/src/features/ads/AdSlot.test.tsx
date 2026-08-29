import { act, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { adsAPI } from '@/api/ads';
import { queryCache } from '@/lib/query/cache';
import { AdSlot } from './AdSlot';
import { AD_CONSENT_STORAGE_KEY } from './useAdConsent';

vi.mock('@/api/ads', () => ({
  adsAPI: {
    getConfig: vi.fn(),
    recordTelemetry: vi.fn(),
  },
}));

const mockGetConfig = vi.mocked(adsAPI.getConfig);
const mockRecordTelemetry = vi.mocked(adsAPI.recordTelemetry);

describe('AdSlot component & privacy boundaries', () => {
  beforeEach(() => {
    localStorage.clear();
    queryCache.clear();
    vi.clearAllMocks();
    document.head.innerHTML = '';
  });

  it('renders nothing and injects zero scripts when ads are disabled (self-hosted mode)', async () => {
    localStorage.setItem(AD_CONSENT_STORAGE_KEY, 'granted');
    mockGetConfig.mockResolvedValue({
      enabled: false,
      provider: null,
      publisher_id: null,
    });

    render(<AdSlot placement="sidebar" />);

    expect(document.querySelector('script[src*="ethicalads"]')).toBeNull();
    expect(document.getElementById('lumina-ad-slot-sidebar')).toBeNull();
    expect(mockRecordTelemetry).not.toHaveBeenCalled();
  });

  it('renders nothing and injects zero scripts when consent is not granted', async () => {
    mockGetConfig.mockResolvedValue({
      enabled: true,
      provider: 'ethicalads',
      publisher_id: 'lumina-test',
    });

    render(<AdSlot placement="sidebar" />);

    expect(document.querySelector('script[src*="ethicalads"]')).toBeNull();
    expect(document.getElementById('lumina-ad-slot-sidebar')).toBeNull();
    expect(mockRecordTelemetry).not.toHaveBeenCalled();
  });

  it('injects allowed provider script and mounts ad slot when consent is granted', async () => {
    localStorage.setItem(AD_CONSENT_STORAGE_KEY, 'granted');
    mockGetConfig.mockResolvedValue({
      enabled: true,
      provider: 'ethicalads',
      publisher_id: 'lumina-test',
    });
    mockRecordTelemetry.mockResolvedValue({ recorded: true });

    render(<AdSlot placement="sidebar" />);

    const slot = await screen.findByText('Ad');
    expect(slot).toBeInTheDocument();

    const script = document.querySelector<HTMLScriptElement>('script[src*="ethicalads"]');
    expect(script).not.toBeNull();
    expect(script?.src).toBe('https://media.ethicalads.io/media/client/ethicalads.min.js');

    const container = document.getElementById('lumina-ad-slot-sidebar');
    expect(container).not.toBeNull();
    expect(container).toHaveAttribute('data-ea-publisher', 'lumina-test');
    expect(container).toHaveAttribute('data-ea-placement', 'sidebar');

    // Simulate ad loaded event
    act(() => {
      container?.dispatchEvent(new Event('ea-loaded'));
    });

    expect(mockRecordTelemetry).toHaveBeenCalledWith({
      placement: 'sidebar',
      provider: 'ethicalads',
      status: 'rendered',
    });
  });

  it('handles adblocker script failure gracefully and collapses without crashing', async () => {
    localStorage.setItem(AD_CONSENT_STORAGE_KEY, 'granted');
    mockGetConfig.mockResolvedValue({
      enabled: true,
      provider: 'ethicalads',
      publisher_id: 'lumina-test',
    });
    mockRecordTelemetry.mockResolvedValue({ recorded: true });

    render(<AdSlot placement="footer" />);

    await screen.findByText('Ad');

    const script = document.querySelector<HTMLScriptElement>('script[src*="ethicalads"]');
    expect(script).not.toBeNull();

    // Simulate script loading error (e.g. blocked by uBlock Origin / Brave Shields)
    act(() => {
      script?.dispatchEvent(new Event('error'));
    });

    expect(mockRecordTelemetry).toHaveBeenCalledWith({
      placement: 'footer',
      provider: 'ethicalads',
      status: 'blocked',
    });

    // Container is collapsed cleanly (removed or rendered null)
    expect(document.getElementById('lumina-ad-slot-footer')).toBeNull();
  });

  it('renders Google AdSense ad slot and ins tag when provider is adsense', async () => {
    localStorage.setItem(AD_CONSENT_STORAGE_KEY, 'granted');
    mockGetConfig.mockResolvedValue({
      enabled: true,
      provider: 'adsense',
      publisher_id: 'ca-pub-3125212202463432',
    });
    mockRecordTelemetry.mockResolvedValue({ recorded: true });

    render(<AdSlot placement="dashboard" slotId="1234567890" />);

    const slot = await screen.findByText('Ad');
    expect(slot).toBeInTheDocument();

    const ins = document.querySelector('ins.adsbygoogle');
    expect(ins).not.toBeNull();
    expect(ins).toHaveAttribute('data-ad-client', 'ca-pub-3125212202463432');
    expect(ins).toHaveAttribute('data-ad-slot', '1234567890');

    expect(mockRecordTelemetry).toHaveBeenCalledWith({
      placement: 'dashboard',
      provider: 'adsense',
      status: 'rendered',
    });
  });
});
