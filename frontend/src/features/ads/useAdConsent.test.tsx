import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { AD_CONSENT_STORAGE_KEY, useAdConsent } from './useAdConsent';

describe('useAdConsent hook', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('defaults to pending when no consent is stored in localStorage', () => {
    const { result } = renderHook(() => useAdConsent());
    expect(result.current.consent).toBe('pending');
    expect(result.current.isPending).toBe(true);
    expect(result.current.isGranted).toBe(false);
    expect(result.current.isDenied).toBe(false);
  });

  it('updates state and localStorage when consent is granted', () => {
    const { result } = renderHook(() => useAdConsent());

    act(() => {
      result.current.grantConsent();
    });

    expect(result.current.consent).toBe('granted');
    expect(result.current.isGranted).toBe(true);
    expect(result.current.isPending).toBe(false);
    expect(localStorage.getItem(AD_CONSENT_STORAGE_KEY)).toBe('granted');
  });

  it('updates state and localStorage when consent is denied', () => {
    const { result } = renderHook(() => useAdConsent());

    act(() => {
      result.current.denyConsent();
    });

    expect(result.current.consent).toBe('denied');
    expect(result.current.isDenied).toBe(true);
    expect(result.current.isPending).toBe(false);
    expect(localStorage.getItem(AD_CONSENT_STORAGE_KEY)).toBe('denied');
  });

  it('resets consent back to pending', () => {
    localStorage.setItem(AD_CONSENT_STORAGE_KEY, 'granted');
    const { result } = renderHook(() => useAdConsent());
    expect(result.current.isGranted).toBe(true);

    act(() => {
      result.current.resetConsent();
    });

    expect(result.current.consent).toBe('pending');
    expect(result.current.isPending).toBe(true);
    expect(localStorage.getItem(AD_CONSENT_STORAGE_KEY)).toBeNull();
  });
});
