import { useId } from 'react';
import type { InputHTMLAttributes, ReactNode } from 'react';
import { cx } from '@/lib/cx';
import styles from './Checkbox.module.css';

type ToggleProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'id'> & {
  label: ReactNode;
  description?: ReactNode;
  wrapperClassName?: string;
};

export type CheckboxProps = ToggleProps;

export function Checkbox({
  label,
  description,
  wrapperClassName,
  className,
  ...rest
}: CheckboxProps) {
  const id = useId();
  const descriptionId = `${id}-description`;

  return (
    <label className={cx(styles.wrapper, wrapperClassName)} htmlFor={id}>
      <input
        {...rest}
        id={id}
        type="checkbox"
        aria-describedby={description ? descriptionId : undefined}
        className={cx(styles.input, className)}
      />
      <span className={styles.text}>
        <span className={styles.label}>{label}</span>
        {description ? (
          <span className={styles.description} id={descriptionId}>
            {description}
          </span>
        ) : null}
      </span>
    </label>
  );
}

export type SwitchProps = ToggleProps;

export function Switch({ label, description, wrapperClassName, className, ...rest }: SwitchProps) {
  const id = useId();
  const descriptionId = `${id}-description`;

  return (
    <label className={cx(styles.wrapper, wrapperClassName)} htmlFor={id}>
      <input
        {...rest}
        id={id}
        type="checkbox"
        role="switch"
        aria-describedby={description ? descriptionId : undefined}
        className={cx(styles.switchInput, className)}
      />
      <span className={styles.text}>
        <span className={styles.label}>{label}</span>
        {description ? (
          <span className={styles.description} id={descriptionId}>
            {description}
          </span>
        ) : null}
      </span>
    </label>
  );
}
