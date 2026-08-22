import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { cx } from '@/lib/cx';
import styles from './IconButton.module.css';

export interface IconButtonProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  /** Required: an icon-only control has no visible text to name it. */
  label: string;
  icon: ReactNode;
  size?: 'sm' | 'md' | 'lg';
  tone?: 'default' | 'accent' | 'destructive';
}

export function IconButton({
  label,
  icon,
  size = 'md',
  tone = 'default',
  className,
  type = 'button',
  ...rest
}: IconButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      aria-label={label}
      title={label}
      className={cx(styles.iconButton, styles[size], tone !== 'default' && styles[tone], className)}
    >
      {icon}
    </button>
  );
}
