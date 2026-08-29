import { cx } from '@/lib/cx';
import brandMarkImg from '@/assets/brand-mark.png';
import wordmarkImg from '@/assets/wordmark.png';
import styles from './Brandmark.module.css';

export interface BrandmarkProps {
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

export function Brandmark({ size = 'md', className }: BrandmarkProps) {
  return (
    <img
      src={brandMarkImg}
      alt=""
      className={cx(styles.mark, styles[size], className)}
      aria-hidden="true"
    />
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
      <img
        src={wordmarkImg}
        alt="Lumina"
        className={cx(styles.wordmark, styles[`wordmark_${size}`])}
      />
    </span>
  );
}
