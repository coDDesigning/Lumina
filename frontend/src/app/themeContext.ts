import { createContext, useContext } from 'react';

export type ThemePreference = 'light' | 'dark' | 'system';

export const THEME_STORAGE_KEY = 'lumina.theme';

export interface ThemeApi {
  /** What the user chose. 'system' follows the operating system. */
  preference: ThemePreference;
  /** What is actually rendering right now. */
  resolved: 'light' | 'dark';
  setPreference: (preference: ThemePreference) => void;
}

export const ThemeContext = createContext<ThemeApi | null>(null);

export function useTheme(): ThemeApi {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
}
