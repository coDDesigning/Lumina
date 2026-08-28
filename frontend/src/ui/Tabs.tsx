import { useRef } from 'react';
import type { KeyboardEvent } from 'react';
import { Link } from 'react-router-dom';
import { ArrowUpRight } from 'lucide-react';
import { cx } from '@/lib/cx';
import styles from './Tabs.module.css';

export interface TabOption<T extends string> {
  value: T;
  label: string;
  controls?: string;
}

export interface TabLink {
  to: string;
  label: string;
}

export interface TabsProps<T extends string> {
  label: string;
  options: TabOption<T>[];
  value: T;
  onChange: (value: T) => void;
  /**
   * A destination that belongs beside the tabs but leaves the screen.
   *
   * Rendered outside the tablist and marked with an arrow, because a control
   * that navigates must never claim `role="tab"`: `aria-selected` promises a
   * panel swapping in place, and someone who activated it would be moved to
   * another page with no warning. It also stays out of the arrow-key roving
   * order, which belongs to the tabs alone.
   */
  link?: TabLink;
  className?: string;
}

export function Tabs<T extends string>({
  label,
  options,
  value,
  onChange,
  link,
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
    <div className={cx(styles.group, className)}>
      <div
        ref={listRef}
        role="tablist"
        aria-label={label}
        onKeyDown={handleKeyDown}
        className={styles.tablist}
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

      {link ? (
        <Link className={styles.link} to={link.to}>
          {link.label}
          <ArrowUpRight className={styles.linkIcon} aria-hidden="true" />
        </Link>
      ) : null}
    </div>
  );
}
