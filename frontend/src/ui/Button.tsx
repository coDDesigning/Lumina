import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { Spinner } from './Spinner';
import styles from './Button.module.css';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'destructive';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  isLoading?: boolean;
  loadingLabel?: string;
  fullWidth?: boolean;
  alignStart?: boolean;
  wrap?: boolean;
  icon?: ReactNode;
  iconAfter?: ReactNode;
}

export function Button({
  variant = 'secondary',
  size = 'md',
  isLoading = false,
  loadingLabel,
  fullWidth = false,
  alignStart = false,
  wrap = false,
  icon,
  iconAfter,
  className,
  children,
  disabled,
  type = 'button',
  onClick,
  'aria-disabled': ariaDisabled,
  ...rest
}: ButtonProps) {
  const isBusy = isLoading && !disabled;
  const isInert = isBusy || ariaDisabled === true || ariaDisabled === 'true';

  return (
    <button
      {...rest}
      type={type}
      disabled={disabled}
      aria-disabled={isInert || undefined}
      aria-busy={isLoading || undefined}
      onClick={
        isBusy
          ? (event) => {
              event.preventDefault();
            }
          : onClick
      }
      className={cx(
        styles.button,
        styles[variant],
        styles[size],
        fullWidth && styles.fullWidth,
        alignStart && styles.alignStart,
        wrap && styles.wrap,
        className,
      )}
    >
      {isLoading ? <Spinner size="sm" label={loadingLabel} /> : icon}
      {children}
      {isLoading ? null : iconAfter}
    </button>
  );
}
