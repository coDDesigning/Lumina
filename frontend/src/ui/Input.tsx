import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from 'react';
import { cx } from '@/lib/cx';
import { Field } from './Field';
import styles from './Field.module.css';

type FieldShellProps = {
  label: string;
  hint?: ReactNode;
  error?: string;
  optional?: boolean;
  hideLabel?: boolean;
  fieldClassName?: string;
};

export type InputProps = FieldShellProps &
  Omit<InputHTMLAttributes<HTMLInputElement>, 'id' | 'aria-describedby' | 'aria-invalid'>;

export function Input({
  label,
  hint,
  error,
  optional,
  hideLabel,
  fieldClassName,
  className,
  ...rest
}: InputProps) {
  return (
    <Field
      label={label}
      hint={hint}
      error={error}
      optional={optional}
      hideLabel={hideLabel}
      className={fieldClassName}
    >
      {({ id, describedBy, invalid, className: controlClass }) => (
        <input
          {...rest}
          id={id}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          className={cx(controlClass, className)}
        />
      )}
    </Field>
  );
}

export type TextareaProps = FieldShellProps &
  Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'id' | 'aria-describedby' | 'aria-invalid'>;

export function Textarea({
  label,
  hint,
  error,
  optional,
  hideLabel,
  fieldClassName,
  className,
  ...rest
}: TextareaProps) {
  return (
    <Field
      label={label}
      hint={hint}
      error={error}
      optional={optional}
      hideLabel={hideLabel}
      className={fieldClassName}
    >
      {({ id, describedBy, invalid, className: controlClass }) => (
        <textarea
          {...rest}
          id={id}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          className={cx(controlClass, styles.textarea, className)}
        />
      )}
    </Field>
  );
}

export type SelectProps = FieldShellProps &
  Omit<SelectHTMLAttributes<HTMLSelectElement>, 'id' | 'aria-describedby' | 'aria-invalid'>;

export function Select({
  label,
  hint,
  error,
  optional,
  hideLabel,
  fieldClassName,
  className,
  children,
  ...rest
}: SelectProps) {
  return (
    <Field
      label={label}
      hint={hint}
      error={error}
      optional={optional}
      hideLabel={hideLabel}
      className={fieldClassName}
    >
      {({ id, describedBy, invalid, className: controlClass }) => (
        <select
          {...rest}
          id={id}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          className={cx(controlClass, styles.select, className)}
        >
          {children}
        </select>
      )}
    </Field>
  );
}
