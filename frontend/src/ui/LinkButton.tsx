import type { AnchorHTMLAttributes, ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { cx } from '@/lib/cx';
import type { ButtonSize, ButtonVariant } from './Button';
import styles from './Button.module.css';

interface SharedProps {
  variant?: ButtonVariant;
  size?: ButtonSize;
  fullWidth?: boolean;
  icon?: ReactNode;
  className?: string;
  children?: ReactNode;
}

export interface LinkButtonProps extends SharedProps {
  to: string;
}

export function LinkButton({
  to,
  variant = 'secondary',
  size = 'md',
  fullWidth = false,
  icon,
  className,
  children,
}: LinkButtonProps) {
  return (
    <Link
      to={to}
      className={cx(
        styles.button,
        styles[variant],
        styles[size],
        fullWidth && styles.fullWidth,
        className,
      )}
    >
      {icon}
      {children}
    </Link>
  );
}

export interface ExternalLinkButtonProps
  extends SharedProps,
    Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'className' | 'children'> {
  href: string;
}

export function ExternalLinkButton({
  href,
  variant = 'secondary',
  size = 'md',
  fullWidth = false,
  icon,
  className,
  children,
  ...rest
}: ExternalLinkButtonProps) {
  return (
    <a
      {...rest}
      href={href}
      className={cx(
        styles.button,
        styles[variant],
        styles[size],
        fullWidth && styles.fullWidth,
        className,
      )}
    >
      {icon}
      {children}
    </a>
  );
}
