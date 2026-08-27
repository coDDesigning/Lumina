import { useState } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';
import { X } from 'lucide-react';
import { cx } from '@/lib/cx';
import { Field } from './Field';
import fieldStyles from './Field.module.css';
import styles from './TagInput.module.css';

export interface TagInputProps {
  label: string;
  value: readonly string[];
  onChange: (next: string[]) => void;
  hint?: ReactNode;
  error?: string;
  optional?: boolean;
  hideLabel?: boolean;
  fieldClassName?: string;
  placeholder?: string;
  disabled?: boolean;
  maxItems?: number;
  maxLength?: number;
}

export function TagInput({
  label,
  value,
  onChange,
  hint,
  error,
  optional,
  hideLabel,
  fieldClassName,
  placeholder,
  disabled = false,
  maxItems = 50,
  maxLength = 100,
}: TagInputProps) {
  const [draft, setDraft] = useState('');
  const [rejection, setRejection] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState('');

  const commit = () => {
    const entry = draft.trim();
    if (!entry) {
      return;
    }
    if (entry.length > maxLength) {
      setRejection(`Keep a ${label.toLowerCase()} entry to ${maxLength} characters.`);
      return;
    }
    if (value.some((existing) => existing.toLowerCase() === entry.toLowerCase())) {
      setRejection(`${entry} is already added.`);
      return;
    }
    if (value.length >= maxItems) {
      setRejection(`You can add at most ${maxItems}.`);
      return;
    }
    onChange([...value, entry]);
    setAnnouncement(`${entry} added`);
    setRejection(null);
    setDraft('');
  };

  const remove = (entry: string) => {
    onChange(value.filter((existing) => existing !== entry));
    setAnnouncement(`${entry} removed`);
    setRejection(null);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      commit();
      return;
    }
    if (event.key === 'Backspace' && !draft && value.length > 0) {
      event.preventDefault();
      remove(value[value.length - 1]);
    }
  };

  return (
    <Field
      label={label}
      hint={hint}
      error={error ?? rejection ?? undefined}
      optional={optional}
      hideLabel={hideLabel}
      className={fieldClassName}
    >
      {({ id, describedBy, invalid, className }) => (
        <div className={styles.shell}>
          {value.length > 0 ? (
            <ul className={styles.chips}>
              {value.map((entry) => (
                <li key={entry} className={styles.chip}>
                  <span className={styles.chipText}>{entry}</span>
                  {disabled ? null : (
                    <button
                      type="button"
                      className={styles.remove}
                      onClick={() => remove(entry)}
                      aria-label={`Remove ${entry}`}
                    >
                      <X aria-hidden="true" />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          ) : null}
          <input
            id={id}
            type="text"
            className={cx(className, fieldStyles.control)}
            value={draft}
            placeholder={placeholder}
            disabled={disabled}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            onChange={(event) => {
              setDraft(event.target.value);
              setRejection(null);
            }}
            onKeyDown={handleKeyDown}
            onBlur={commit}
          />
          <p className="visually-hidden" role="status">
            {announcement}
          </p>
        </div>
      )}
    </Field>
  );
}
