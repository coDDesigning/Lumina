import { Link } from 'react-router-dom';
import { useLegalPolicies } from './useLegalPolicies';
import styles from './LegalFooter.module.css';

const links = [
  ['/legal/privacy', 'Privacy'],
  ['/legal/terms', 'Terms'],
  ['/legal/acceptable-use', 'Acceptable use'],
  ['/legal/cookies', 'Cookies'],
  ['/legal/ai-disclosure', 'AI disclosure'],
  ['/legal/security', 'Security'],
  ['/legal/open-source', 'Open source'],
] as const;

export function LegalFooter() {
  const { enabled } = useLegalPolicies();

  if (!enabled) {
    return null;
  }

  return (
    <footer className={styles.footer}>
      <div className={styles.inner}>
        <span>© 2026 coDDesigning contributors</span>
        <nav className={styles.links} aria-label="Legal">
          {links.map(([to, label]) => (
            <Link key={to} to={to}>{label}</Link>
          ))}
        </nav>
      </div>
    </footer>
  );
}
