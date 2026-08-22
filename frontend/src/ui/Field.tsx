import { useId } from 'react';
import type { ReactNode } from 'react';
import { AlertCircle } from 'lucide-react';
import { cx } from '@/lib/cx';
import styles from './Field.module.css';

export interface FieldRenderArgs {
  id: string;
  describedBy: string | undefined;
  invalid: boolean;
  className: string;
}

export interface FieldProps {
  label: string;
  hint?: ReactNode;
  error?: string;
  optional?: boolean;
  hideLabel?: boolean;
  className?: string;
  children: (args: FieldRenderArgs) => ReactNode;
}

/**
 * Wires label, hint and validation message to the control with real ids, so a
 * screen reader announces the error with the field rather than in isolation.
 */
export function Field({
  label,
  hint,
  error,
  optional = false,
  hideLabel = false,
  className,
  children,
}: FieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;

  const describedBy =
    [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(' ') || undefined;

  return (
    <div className={cx(styles.field, className)}>
      <label className={cx(styles.label, hideLabel && 'visually-hidden')} htmlFor={id}>
        {label}
        {optional ? <span className={styles.optional}> (optional)</span> : null}
      </label>
      {children({ id, describedBy, invalid: Boolean(error), className: styles.control })}
      {hint ? (
        <p className={styles.hint} id={hintId}>
          {hint}
        </p>
      ) : null}
      {error ? (
        <p className={styles.error} id={errorId}>
          <AlertCircle aria-hidden="true" />
          {error}
        </p>
      ) : null}
    </div>
  );
}
