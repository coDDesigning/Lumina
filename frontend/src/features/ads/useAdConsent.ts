import { useCallback, useEffect, useState } from 'react';

export type AdConsentStatus = 'pending' | 'granted' | 'denied';

export const AD_CONSENT_STORAGE_KEY = 'lumina_ad_consent';

export function getStoredAdConsent(): AdConsentStatus {
  try {
    const val = localStorage.getItem(AD_CONSENT_STORAGE_KEY);
    if (val === 'granted' || val === 'denied') {
      return val;
    }
  } catch {
    // Ignore localStorage access exceptions in restricted environments
  }
  return 'pending';
}

export function useAdConsent() {
  const [consent, setConsent] = useState<AdConsentStatus>(getStoredAdConsent);

  useEffect(() => {
    const handleStorage = (event: StorageEvent) => {
      if (event.key === AD_CONSENT_STORAGE_KEY) {
        setConsent(getStoredAdConsent());
      }
    };
    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, []);

  const grantConsent = useCallback(() => {
    try {
      localStorage.setItem(AD_CONSENT_STORAGE_KEY, 'granted');
    } catch {
      // Ignore storage errors
    }
    setConsent('granted');
  }, []);

  const denyConsent = useCallback(() => {
    try {
      localStorage.setItem(AD_CONSENT_STORAGE_KEY, 'denied');
    } catch {
      // Ignore storage errors
    }
    setConsent('denied');
  }, []);

  const resetConsent = useCallback(() => {
    try {
      localStorage.removeItem(AD_CONSENT_STORAGE_KEY);
    } catch {
      // Ignore storage errors
    }
    setConsent('pending');
  }, []);

  return {
    consent,
    isGranted: consent === 'granted',
    isDenied: consent === 'denied',
    isPending: consent === 'pending',
    grantConsent,
    denyConsent,
    resetConsent,
  };
}
