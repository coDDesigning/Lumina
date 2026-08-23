import type { ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { Alert } from './Alert';
import { EmptyState } from './EmptyState';
import { Spinner } from './Spinner';
import styles from './MasterDetail.module.css';

export type LoadPhase<T> =
  | { phase: 'loading' }
  | { phase: 'ready'; data: T }
  | { phase: 'error'; message: string };

export interface MasterDetailProps<T> {
  listLabel: string;
  items: T[];
  keyOf: (item: T) => number;
  labelOf: (item: T) => string;
  renderItem: (item: T) => ReactNode;
  selectedKey: number | null;
  onSelect: (item: T) => void;
  emptyList: ReactNode;
  detail: ReactNode;
}

export function MasterDetail<T>({
  listLabel,
  items,
  keyOf,
  labelOf,
  renderItem,
  selectedKey,
  onSelect,
  emptyList,
  detail,
}: MasterDetailProps<T>) {
  if (items.length === 0) {
    return <>{emptyList}</>;
  }

  return (
    <div className={styles.layout}>
      <nav className={styles.list} aria-label={listLabel}>
        <ul>
          {items.map((item) => {
            const key = keyOf(item);
            const isSelected = key === selectedKey;
            return (
              <li key={key}>
                <button
                  type="button"
                  className={cx(styles.entry, isSelected && styles.selected)}
                  aria-current={isSelected ? 'true' : undefined}
                  aria-label={labelOf(item)}
                  onClick={() => onSelect(item)}
                >
                  {renderItem(item)}
                </button>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className={styles.detail}>{detail}</div>
    </div>
  );
}

export function DetailPlaceholder({ title, body }: { title: string; body: string }) {
  return <EmptyState title={title} description={body} headingLevel="h3" />;
}

export function DetailLoading({ label }: { label: string }) {
  return (
    <div className={styles.pending} role="status">
      <Spinner />
      <p>{label}</p>
    </div>
  );
}

export function DetailError({ message }: { message: string }) {
  return (
    <Alert tone="destructive" live="alert">
      {message}
    </Alert>
  );
}
