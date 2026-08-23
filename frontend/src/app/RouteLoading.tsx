import { Spinner } from '@/ui/Spinner';
import styles from './RouteLoading.module.css';

export interface RouteLoadingProps {
  label: string;
}

export function RouteLoading({ label }: RouteLoadingProps) {
  return (
    <div className={styles.wrap} role="status">
      <Spinner size="lg" />
      <p className={styles.label}>{label}</p>
    </div>
  );
}
