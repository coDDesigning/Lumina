import type { HTMLAttributes, ReactNode } from 'react';
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

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
  icon?: ReactNode;
  children: ReactNode;
}

export function Badge({ tone = 'neutral', icon, className, children, ...rest }: BadgeProps) {
  return (
    <span {...rest} className={cx(styles.badge, styles[tone], className)}>
      {icon}
      {children}
    </span>
  );
}
