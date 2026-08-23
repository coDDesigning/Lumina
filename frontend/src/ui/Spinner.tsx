import { cx } from '@/lib/cx';
import styles from './Spinner.module.css';

export interface SpinnerProps {
  size?: 'sm' | 'md' | 'lg';
  className?: string;
  label?: string;
}

export function Spinner({ size = 'md', className, label }: SpinnerProps) {
  return (
    <>
      <svg
        className={cx(styles.spinner, styles[size], className)}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        aria-hidden="true"
        focusable="false"
      >
        <path d="M21 12a9 9 0 1 1-6.219-8.56" />
      </svg>
      {label ? <span className="visually-hidden">{label}</span> : null}
    </>
  );
}
