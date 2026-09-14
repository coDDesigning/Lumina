import { Link } from 'react-router-dom';
import { useLegalPolicies } from './useLegalPolicies';
import styles from './AiDisclosureNotice.module.css';

export function AiDisclosureNotice() {
  const { enabled } = useLegalPolicies();

  if (!enabled) {
    return null;
  }

  return (
    <p className={styles.notice}>
      AI-generated content can be wrong. <Link to="/legal/ai-disclosure">How Lumina uses AI</Link>
    </p>
  );
}
