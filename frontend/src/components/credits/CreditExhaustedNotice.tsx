import { RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useCredits } from '../../context/CreditContext';
import type { CreditSource } from '../../api/types';
import { cx } from '@/lib/cx';
import { Button } from '@/ui/Button';
import styles from './CreditExhaustedNotice.module.css';

type CreditExhaustedNoticeProps = {
  source: CreditSource;
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
  const isUnverified = Boolean(status?.email_verification_required && !status?.is_email_verified);
  const grantDate = formatGrantDate(status?.next_grant_at ?? null);
  const monthlyGrant = status?.monthly_grant ?? null;

  return (
    <div className={cx(styles.notice, className)} role="alert">
      <h4 className={styles.heading}>
        You don&apos;t have enough credits to generate {action}.
      </h4>
      <p className={styles.body}>
        {cost !== null
          ? `This costs ${cost} ${cost === 1 ? 'credit' : 'credits'} and you have ${balance} left.`
          : `You have ${balance} credits left.`}
      </p>
      <ul className={styles.routes}>
        {isUnverified ? (
          <>
            <li>
              Confirm your email address to receive your starting credits. We sent a verification link to your inbox.
            </li>
            <li>
              Need a new link? <Link to="/verify-email">Resend verification email</Link>.
            </li>
          </>
        ) : (
          <>
            {grantDate ? (
              <li>
                {`Your credits refresh on ${grantDate}${
                  monthlyGrant !== null ? ` (up to ${monthlyGrant} more)` : ''
                }.`}
              </li>
            ) : null}
            <li>
              Need them sooner? Contact an administrator, who can add credits to your
              account straight away.
            </li>
          </>
        )}
      </ul>
      <div className={styles.actions}>
        <Button
          size="sm"
          onClick={() => void handleRefresh()}
          isLoading={refreshing}
          loadingLabel="Refreshing your balance"
          icon={<RefreshCw aria-hidden="true" />}
        >
          {refreshing ? 'Refreshing' : 'Refresh balance'}
        </Button>
      </div>
    </div>
  );
}

export default CreditExhaustedNotice;
