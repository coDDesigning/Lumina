import { Coins } from 'lucide-react';
import { useCredits } from '../../context/CreditContext';
import type { CreditSource } from '../../api/types';
import { cx } from '@/lib/cx';
import styles from './CreditBalance.module.css';

const LOW_BALANCE_THRESHOLD = 5;

type CreditBalanceProps = {
  source?: CreditSource;
  className?: string;
};

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
