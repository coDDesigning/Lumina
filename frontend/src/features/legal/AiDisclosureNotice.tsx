import { Link } from 'react-router-dom';
import { cx } from '@/lib/cx';
import { Alert } from '@/ui/Alert';
import { useLegalPolicies } from './useLegalPolicies';
import styles from './AiDisclosureNotice.module.css';

export function AiDisclosureNotice({ className }: { className?: string }) {
  const { enabled } = useLegalPolicies();

  if (!enabled) {
    return null;
  }

  return (
    <Alert className={cx(styles.notice, className)} tone="warning" title="AI-assisted study content">
      Outputs and learning estimates can be wrong. Verify important information with your course
      materials or instructor. <Link to="/legal/ai-disclosure">How Lumina uses AI</Link>
    </Alert>
  );
}
