import { createContext, useContext } from 'react';

export type ToastTone = 'success' | 'error' | 'info';

export interface ToastInput {
  tone?: ToastTone;
  title: string;
  message?: string;
  durationMs?: number;
}

export interface ToastApi {
  showToast: (toast: ToastInput) => void;
  dismissToast: (id: number) => void;
}

export const ToastContext = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within a ToastProvider');
  }
  return context;
}
