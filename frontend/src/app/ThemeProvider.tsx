import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { THEME_STORAGE_KEY, ThemeContext } from './themeContext';
import type { ThemeApi, ThemePreference } from './themeContext';

function readStoredPreference(): ThemePreference {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === 'light' || stored === 'dark' || stored === 'system') {
      return stored;
    }
  } catch {
    // Private browsing and blocked site data both throw here; the default is fine.
  }
  return 'system';
}

function systemPrefersDark(): boolean {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(readStoredPreference);
  const [systemDark, setSystemDark] = useState(systemPrefersDark);

  useEffect(() => {
    const query = window.matchMedia?.('(prefers-color-scheme: dark)');
    if (!query) {
      return;
    }

    function handleChange(event: MediaQueryListEvent) {
      setSystemDark(event.matches);
    }

    query.addEventListener('change', handleChange);
    return () => query.removeEventListener('change', handleChange);
  }, []);

  const resolved: 'light' | 'dark' =
    preference === 'system' ? (systemDark ? 'dark' : 'light') : preference;

  useEffect(() => {
    const root = document.documentElement;
    if (preference === 'system') {
      root.removeAttribute('data-theme');
    } else {
      root.setAttribute('data-theme', preference);
    }
    root.style.colorScheme = resolved;
  }, [preference, resolved]);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Storing the choice is a convenience; failing to store it must not break the app.
    }
  }, []);

  const api = useMemo<ThemeApi>(
    () => ({ preference, resolved, setPreference }),
    [preference, resolved, setPreference],
  );

  return <ThemeContext.Provider value={api}>{children}</ThemeContext.Provider>;
}
