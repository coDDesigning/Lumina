import { cx } from '@/lib/cx';
import styles from './Brandmark.module.css';

export interface BrandmarkProps {
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

export function Brandmark({ size = 'md', className }: BrandmarkProps) {
  return (
    <span className={cx(styles.mark, styles[size], className)} aria-hidden="true">
      L
    </span>
  );
}

export interface BrandLockupProps {
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

export function BrandLockup({ size = 'sm', className }: BrandLockupProps) {
  return (
    <span className={cx(styles.lockup, className)}>
      <Brandmark size={size} />
      <span className={styles.wordmark}>Lumina</span>
    </span>
  );
}
