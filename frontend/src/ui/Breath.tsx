import { cx } from '@/lib/cx';
import styles from './Breath.module.css';

export interface BreathProps {
  count?: 1 | 3;
  className?: string;
  label?: string;
}

/**
 * The calm progress signal: a slow pulse rather than a spinner, for work that
 * takes tens of seconds. Reduced motion turns it into a static dot.
 */
export function Breath({ count = 1, className, label }: BreathProps) {
  const dots = count === 3 ? [0, 1, 2] : [0];

  return (
    <span className={cx(styles.breath, className)} aria-hidden={label ? undefined : true}>
      {dots.map((dot) => (
        <span key={dot} className={styles.dot} />
      ))}
      {label ? <span className="visually-hidden">{label}</span> : null}
    </span>
  );
}
