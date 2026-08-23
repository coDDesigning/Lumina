import { Coins } from 'lucide-react';
import { useCredits } from '../../context/CreditContext';
import type { CreditSource } from '../../api/types';
import { cx } from '@/lib/cx';
import styles from './CreditBalance.module.css';

const LOW_BALANCE_THRESHOLD = 5;

type CreditBalanceProps = {
  /** When given, the balance reads as empty once it cannot cover this action. */
  source?: CreditSource;
  className?: string;
};

/**
 * The remaining balance, or nothing at all when credits do not apply.
 *
 * A balance that is merely loading or unreadable is never rendered as zero:
 * those are different states from an exhausted account and lead the reader to
 * a different next step.
 */
function CreditBalance({ source, className }: CreditBalanceProps) {
  const { status, isLoading, error, isMetered, canAfford } = useCredits();

  if (!isMetered) {
    if (isLoading || error) {
      return (
        <span
          className={cx(styles.chip, styles.unknown, className)}
          aria-label={error ?? 'Loading credit balance'}
        >
          <Coins aria-hidden="true" />
          <span>{error ? 'Balance unavailable' : '—'}</span>
        </span>
      );
    }
    return null;
  }

  const credits = status?.credits ?? 0;
  const exhausted = source ? !canAfford(source) : credits <= 0;
  const low = !exhausted && credits <= LOW_BALANCE_THRESHOLD;
  return (
    <span
      className={cx(styles.chip, exhausted && styles.empty, low && styles.low, className)}
    >
      <Coins aria-hidden="true" />
      <span>
        {credits}
        <span className={styles.unit}> credits</span>
      </span>
    </span>
  );
}

export default CreditBalance;
