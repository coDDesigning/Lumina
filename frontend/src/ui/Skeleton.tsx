import { cx } from '@/lib/cx';
import styles from './Skeleton.module.css';

export interface SkeletonProps {
  variant?: 'text' | 'heading' | 'block';
  width?: string;
  height?: string;
  className?: string;
}

export function Skeleton({ variant = 'text', width, height, className }: SkeletonProps) {
  return (
    <span
      className={cx(styles.skeleton, styles[variant], className)}
      style={{ width, height }}
      aria-hidden="true"
    />
  );
}

export interface SkeletonTextProps {
  lines?: number;
  className?: string;
}

const LINE_WIDTHS = ['100%', '94%', '86%', '72%', '90%'];

export function SkeletonText({ lines = 3, className }: SkeletonTextProps) {
  return (
    <span className={cx(styles.group, className)} aria-hidden="true">
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton key={index} width={LINE_WIDTHS[index % LINE_WIDTHS.length]} />
      ))}
    </span>
  );
}
