import type { ReactNode } from 'react';
import { cx } from '@/lib/cx';
import styles from './Badge.module.css';

export type BadgeTone =
  | 'neutral'
  | 'accent'
  | 'success'
  | 'processing'
  | 'warning'
  | 'destructive'
  | 'info';

export interface BadgeProps {
  tone?: BadgeTone;
  icon?: ReactNode;
  className?: string;
  children: ReactNode;
}

/**
 * Status is never carried by colour alone: a badge always renders its label,
 * and status badges pass an icon as a third channel.
 */
export function Badge({ tone = 'neutral', icon, className, children }: BadgeProps) {
  return (
    <span className={cx(styles.badge, styles[tone], className)}>
      {icon}
      {children}
    </span>
  );
}
