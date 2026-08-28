import { useQuery } from '@/lib/query/useQuery';
import { queryKeys } from '@/api/queryKeys';
import { adsAPI } from '@/api/ads';
import { Button } from '@/ui/Button';
import { useAdConsent } from './useAdConsent';
import styles from './AdConsentBanner.module.css';

export function AdConsentBanner() {
  const { data: config } = useQuery({
    key: queryKeys.adsConfig(),
    fetcher: ({ signal }) => adsAPI.getConfig({ signal }),
    fallbackMessage: 'Could not load advertising configuration.',
  });

  const { isPending, grantConsent, denyConsent } = useAdConsent();

  if (!config?.enabled || !isPending) {
    return null;
  }

  return (
    <aside
      className={styles.banner}
      aria-label="Advertising and privacy choices"
      role="region"
    >
      <div className={styles.content}>
        <h2 className={styles.title}>Support Lumina with privacy-first ads</h2>
        <p className={styles.description}>
          We display non-personalized, privacy-friendly ads to help support the
          hosted service. Lumina never shares your study materials, prompts,
          uploaded documents, or personal data with advertisers.
        </p>
      </div>
      <div className={styles.actions}>
        <Button variant="ghost" size="sm" onClick={denyConsent}>
          Decline
        </Button>
        <Button variant="primary" size="sm" onClick={grantConsent}>
          Allow
        </Button>
      </div>
    </aside>
  );
}
