import { RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { useCredits } from '../../context/CreditContext';
import type { CreditSource } from '../../api/types';
import './credits.css';

type CreditExhaustedNoticeProps = {
  source: CreditSource;
  /** What the user was trying to make, e.g. "a study guide". */
  action: string;
  className?: string;
};

function formatGrantDate(value: string | null): string | null {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed.toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'long',
  });
}

/**
 * Why generation is unavailable, and the recovery routes that actually exist.
 *
 * The two routes are the monthly grant and an administrator change. There is
 * no purchase path, so this must never offer one.
 */
function CreditExhaustedNotice({
  source,
  action,
  className,
}: CreditExhaustedNoticeProps) {
  const { status, costOf, refresh } = useCredits();
  const [refreshing, setRefreshing] = useState(false);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await refresh();
    } finally {
      setRefreshing(false);
    }
  };

  const balance = status?.credits ?? 0;
  const cost = costOf(source);
  const grantDate = formatGrantDate(status?.next_grant_at ?? null);
  const monthlyGrant = status?.monthly_grant ?? null;

  return (
    <div
      className={`credit-notice${className ? ` ${className}` : ''}`}
      role="alert"
    >
      <h4>You don&apos;t have enough credits to generate {action}.</h4>
      <p>
        {cost !== null
          ? `This costs ${cost} ${cost === 1 ? 'credit' : 'credits'} and you have ${balance} left.`
          : `You have ${balance} credits left.`}
      </p>
      <ul>
        <li>
          {grantDate
            ? `Your credits refresh on ${grantDate}${
                monthlyGrant !== null ? ` (up to ${monthlyGrant} more)` : ''
              }.`
            : 'Your credits refresh at the start of next month.'}
        </li>
        <li>
          Need them sooner? Contact an administrator, who can add credits to your
          account straight away.
        </li>
      </ul>
      <div className="credit-notice-actions">
        <button
          type="button"
          className="credit-refresh-button"
          onClick={handleRefresh}
          disabled={refreshing}
        >
          <RefreshCw aria-hidden="true" />
          {refreshing ? 'Refreshing…' : 'Refresh balance'}
        </button>
      </div>
    </div>
  );
}

export default CreditExhaustedNotice;
