import { useMemo } from 'react';
import { Plus } from 'lucide-react';
import { Button } from '@/ui/Button';
import styles from './TopicSuggestions.module.css';

export interface TopicSuggestionsProps {
  headingId: string;
  title: string;
  lede: string;
  suggestions: readonly string[];
  declared: readonly string[];
  onAdd: (topics: string[]) => void;
  disabled?: boolean;
}

export function TopicSuggestions({
  headingId,
  title,
  lede,
  suggestions,
  declared,
  onAdd,
  disabled,
}: TopicSuggestionsProps) {
  const missing = useMemo(() => {
    const inBox = new Set(declared.map((topic) => topic.trim().toLowerCase()));
    const seen = new Set<string>();
    const result: string[] = [];
    for (const label of suggestions) {
      const key = label.trim().toLowerCase();
      if (!key || inBox.has(key) || seen.has(key)) {
        continue;
      }
      seen.add(key);
      result.push(label);
    }
    return result;
  }, [suggestions, declared]);

  if (disabled || missing.length === 0) {
    return null;
  }

  return (
    <section className={styles.block} aria-labelledby={headingId}>
      <h3 id={headingId} className={styles.label}>
        {title}
      </h3>
      <p className={styles.lede}>{lede}</p>
      <ul className={styles.list}>
        {missing.map((label) => (
          <li key={label}>
            <Button
              variant="secondary"
              size="sm"
              wrap
              icon={<Plus aria-hidden="true" />}
              onClick={() => onAdd([label])}
            >
              {label}
            </Button>
          </li>
        ))}
      </ul>
      {missing.length > 1 ? (
        <Button variant="ghost" size="sm" onClick={() => onAdd(missing)}>
          Add all {missing.length}
        </Button>
      ) : null}
    </section>
  );
}
