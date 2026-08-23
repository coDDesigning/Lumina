import type { ReactNode } from 'react';
import { RotateCw } from 'lucide-react';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { Spinner } from '@/ui/Spinner';
import styles from './GenerationStates.module.css';

export interface GeneratingStateProps {
  heading: string;
  detail: string;
  elapsed: number;
}

export function GeneratingState({ heading, detail, elapsed }: GeneratingStateProps) {
  return (
    <div className={styles.pending} role="status">
      <Spinner size="lg" />
      <p className={styles.pendingHeading}>{heading}</p>
      <p className={styles.pendingDetail}>{detail}</p>
      <p className={styles.elapsed}>
        <span className="tabular">{elapsed}</span>s
      </p>
    </div>
  );
}

export interface GenerationErrorProps {
  message: string;
  retryable: boolean;
  onRetry: () => void;
}

export function GenerationError({ message, retryable, onRetry }: GenerationErrorProps) {
  return (
    <div className={styles.failure}>
      <Alert tone="destructive" live="alert" title="That did not generate">
        {message}
      </Alert>
      {retryable ? (
        <Button variant="primary" onClick={onRetry} icon={<RotateCw aria-hidden="true" />}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}

export interface SetupPanelProps {
  lede: string;
  children?: ReactNode;
}

export function SetupPanel({ lede, children }: SetupPanelProps) {
  return (
    <div className={styles.setup}>
      <p className={styles.lede}>{lede}</p>
      {children}
    </div>
  );
}

export function NoMaterialNotice({ what }: { what: string }) {
  return (
    <Alert tone="warning" title="There is nothing to work from yet">
      {what} is built from your own course material. Upload a document and wait for it to finish
      processing, then come back.
    </Alert>
  );
}
