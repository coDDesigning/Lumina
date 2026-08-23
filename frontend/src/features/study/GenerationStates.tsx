import type { ReactNode } from 'react';
import { RotateCw } from 'lucide-react';
import type { GenerationFailure } from '@/api/errors';
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
  failure: GenerationFailure;
  onRetry: () => void;
  onBroadenTopic?: () => void;
  onSeeSources?: () => void;
}

export function GenerationError({
  failure,
  onRetry,
  onBroadenTopic,
  onSeeSources,
}: GenerationErrorProps) {
  const remedies = [];

  if (failure.remedy === 'broaden_topic' && onBroadenTopic) {
    remedies.push(
      <Button key="broaden" variant="primary" onClick={onBroadenTopic}>
        Use every topic
      </Button>,
    );
  }
  if ((failure.remedy === 'see_sources' || failure.remedy === 'add_source') && onSeeSources) {
    remedies.push(
      <Button key="sources" variant="primary" onClick={onSeeSources}>
        See your sources
      </Button>,
    );
  }
  if (failure.retryable) {
    remedies.push(
      <Button
        key="retry"
        variant={remedies.length > 0 ? 'secondary' : 'primary'}
        onClick={onRetry}
        icon={<RotateCw aria-hidden="true" />}
      >
        Try again
      </Button>,
    );
  }

  return (
    <div className={styles.failure}>
      <Alert tone="destructive" live="alert" title={failure.title}>
        {failure.message}
      </Alert>
      {remedies.length > 0 ? <div className={styles.remedies}>{remedies}</div> : null}
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
