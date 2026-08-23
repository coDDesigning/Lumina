import type { ReactNode } from 'react';
import { AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-react';
import { cx } from '@/lib/cx';
import styles from './Alert.module.css';

export type AlertTone = 'info' | 'success' | 'warning' | 'destructive' | 'accent';

const TONE_ICON = {
  info: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  destructive: XCircle,
  accent: Info,
} as const;

export interface AlertProps {
  tone?: AlertTone;
  title?: string;
  children?: ReactNode;
  actions?: ReactNode;
  className?: string;
  live?: 'alert' | 'status' | 'none';
}

export function Alert({
  tone = 'info',
  title,
  children,
  actions,
  className,
  live = 'none',
}: AlertProps) {
  const Icon = TONE_ICON[tone];

  return (
    <div
      className={cx(styles.alert, styles[tone], className)}
      role={live === 'none' ? undefined : live}
      aria-live={live === 'status' ? 'polite' : undefined}
    >
      <Icon className={styles.icon} aria-hidden="true" />
      <div className={styles.body}>
        {title ? <p className={styles.title}>{title}</p> : null}
        {children ? <div className={styles.message}>{children}</div> : null}
        {actions ? <div className={styles.actions}>{actions}</div> : null}
      </div>
    </div>
  );
}
