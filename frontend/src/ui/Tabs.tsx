import { useRef } from 'react';
import type { KeyboardEvent } from 'react';
import { cx } from '@/lib/cx';
import styles from './Tabs.module.css';

export interface TabOption<T extends string> {
  value: T;
  label: string;
  controls?: string;
}

export interface TabsProps<T extends string> {
  label: string;
  options: TabOption<T>[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
}

/**
 * Segmented control with roving arrow-key navigation, per the WAI tabs pattern.
 */
export function Tabs<T extends string>({
  label,
  options,
  value,
  onChange,
  className,
}: TabsProps<T>) {
  const listRef = useRef<HTMLDivElement>(null);

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const index = options.findIndex((option) => option.value === value);
    if (index < 0) {
      return;
    }

    let nextIndex: number | null = null;
    if (event.key === 'ArrowRight') {
      nextIndex = (index + 1) % options.length;
    } else if (event.key === 'ArrowLeft') {
      nextIndex = (index - 1 + options.length) % options.length;
    } else if (event.key === 'Home') {
      nextIndex = 0;
    } else if (event.key === 'End') {
      nextIndex = options.length - 1;
    }

    if (nextIndex === null) {
      return;
    }

    event.preventDefault();
    onChange(options[nextIndex].value);
    const buttons = listRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    buttons?.[nextIndex]?.focus();
  }

  return (
    <div
      ref={listRef}
      role="tablist"
      aria-label={label}
      onKeyDown={handleKeyDown}
      className={cx(styles.tablist, className)}
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={option.controls}
            tabIndex={selected ? 0 : -1}
            className={styles.tab}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
