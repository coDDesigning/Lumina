import { useTheme } from '@/app/themeContext';
import type { ThemePreference } from '@/app/themeContext';
import { Select } from '@/ui/Input';
import styles from './AccountPage.module.css';

export default function AccountAppearancePage() {
  const { preference, setPreference } = useTheme();

  return (
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
  );
}
