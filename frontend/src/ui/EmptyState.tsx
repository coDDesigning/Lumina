import type { ReactNode } from 'react';
import { cx } from '@/lib/cx';
import styles from './EmptyState.module.css';

export interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  footnote?: ReactNode;
  tone?: 'accent' | 'destructive';
  className?: string;
  headingLevel?: 'h1' | 'h2' | 'h3';
}

export function EmptyState({
  icon,
  title,
  description,
  actions,
  footnote,
  tone = 'accent',
  className,
  headingLevel = 'h2',
}: EmptyStateProps) {
  const Heading = headingLevel;

  return (
    <div className={cx(styles.empty, className)}>
      <div className={styles.inner}>
        {icon ? (
          <span className={cx(styles.mark, tone === 'destructive' && styles.markDestructive)}>
            {icon}
          </span>
        ) : null}
        <Heading className={styles.title}>{title}</Heading>
        {description ? <p className={styles.description}>{description}</p> : null}
        {actions ? <div className={styles.actions}>{actions}</div> : null}
        {footnote ? <p className={styles.footnote}>{footnote}</p> : null}
      </div>
    </div>
  );
}
