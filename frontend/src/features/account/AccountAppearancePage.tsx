import { useTheme } from '@/app/themeContext';
import type { ThemePreference } from '@/app/themeContext';
import { Select } from '@/ui/Input';
import { useQuery } from '@/lib/query/useQuery';
import { queryKeys } from '@/api/queryKeys';
import { adsAPI } from '@/api/ads';
import { useAdConsent } from '@/features/ads/useAdConsent';
import styles from './AccountPage.module.css';

export default function AccountAppearancePage() {
  const { preference, setPreference } = useTheme();
  const { data: config } = useQuery({
    key: queryKeys.adsConfig(),
    fetcher: ({ signal }) => adsAPI.getConfig({ signal }),
    fallbackMessage: 'Could not load advertising configuration.',
  });
  const { consent, grantConsent, denyConsent } = useAdConsent();

  return (
    <>
      <section className={styles.section}>
        <h2 className={styles.sectionHeading}>Appearance</h2>
        <p className={styles.sectionLede}>System follows whatever your device is set to.</p>
        <Select
          label="Theme"
          value={preference}
          onChange={(event) => setPreference(event.target.value as ThemePreference)}
        >
          <option value="system">Match my system</option>
          <option value="light">Light</option>
          <option value="dark">Dark</option>
        </Select>
      </section>

      {config?.enabled ? (
        <section className={styles.section}>
          <h2 className={styles.sectionHeading}>Privacy & Advertising</h2>
          <p className={styles.sectionLede}>
            Manage your preference for privacy-preserving, non-personalized advertising.
          </p>
          <Select
            label="Advertising preference"
            value={consent === 'granted' ? 'allowed' : 'declined'}
            onChange={(event) => {
              if (event.target.value === 'allowed') {
                grantConsent();
              } else {
                denyConsent();
              }
            }}
          >
            <option value="allowed">Allow privacy-first ads</option>
            <option value="declined">Decline ads</option>
          </Select>
        </section>
      ) : null}
    </>
  );
}
