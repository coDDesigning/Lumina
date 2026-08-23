import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { CheckCircle2, Info, X, XCircle } from 'lucide-react';
import { cx } from '@/lib/cx';
import { IconButton } from './IconButton';
import { ToastContext } from './toastContext';
import type { ToastApi, ToastInput, ToastTone } from './toastContext';
import styles from './Toast.module.css';

interface ToastRecord {
  id: number;
  tone: ToastTone;
  title: string;
  message?: string;
}

const TONE_ICON = {
  success: CheckCircle2,
  error: XCircle,
  info: Info,
} as const;

const DEFAULT_DURATION = 5000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismissToast = useCallback((id: number) => {
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const showToast = useCallback(
    ({ tone = 'info', title, message, durationMs = DEFAULT_DURATION }: ToastInput) => {
      const id = nextId.current;
      nextId.current += 1;

      setToasts((current) => [...current, { id, tone, title, message }]);

      const timer = setTimeout(() => {
        timers.current.delete(id);
        setToasts((current) => current.filter((toast) => toast.id !== id));
      }, durationMs);
      timers.current.set(id, timer);
    },
    [],
  );

  useEffect(() => {
    const pending = timers.current;
    return () => {
      pending.forEach((timer) => clearTimeout(timer));
      pending.clear();
    };
  }, []);

  const api = useMemo<ToastApi>(() => ({ showToast, dismissToast }), [showToast, dismissToast]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className={styles.region} role="region" aria-label="Notifications">
        {toasts.map((toast) => {
          const Icon = TONE_ICON[toast.tone];
          return (
            <div
              key={toast.id}
              className={cx(styles.toast, styles[toast.tone])}
              role={toast.tone === 'error' ? 'alert' : 'status'}
            >
              <Icon className={styles.icon} aria-hidden="true" />
              <div className={styles.body}>
                <span className={styles.title}>{toast.title}</span>
                {toast.message ? <span className={styles.message}>{toast.message}</span> : null}
              </div>
              <IconButton
                label="Dismiss"
                size="sm"
                icon={<X aria-hidden="true" />}
                onClick={() => dismissToast(toast.id)}
              />
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}
