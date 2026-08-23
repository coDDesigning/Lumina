import { useState } from 'react';
import { AlertTriangle, Check, RotateCcw, Trash2 } from 'lucide-react';
import type { DocumentEntry } from '@/hooks/useCourseDocuments';
import { cx } from '@/lib/cx';
import { Breath } from '@/ui/Breath';
import { Button } from '@/ui/Button';
import { ConfirmDialog } from '@/ui/ConfirmDialog';
import {
  TOTAL_STAGES,
  attemptsLabel,
  describeFailure,
  documentStatusLabel,
  formatFileSize,
  isDocumentBusy,
  materialKindLabel,
  progressLabel,
  stageNumber,
  stageReason,
} from './documentLabels';
import styles from './DocumentRow.module.css';

export interface DocumentRowProps {
  entry: DocumentEntry;
  onRetry: (documentId: string) => void;
  onDelete: (documentId: string) => void;
}

function readyFacts(entry: DocumentEntry): string[] {
  const { document } = entry;
  return [
    materialKindLabel(document.material_kind),
    document.file_type.toUpperCase(),
    formatFileSize(document.file_size),
  ].filter(Boolean);
}

export function DocumentRow({ entry, onRetry, onDelete }: DocumentRowProps) {
  const [isConfirming, setIsConfirming] = useState(false);
  const { document, job } = entry;

  const busy = isDocumentBusy(document.status);
  const failed = document.status === 'failed';
  const ready = document.status === 'ready';

  const stage = progressLabel(job);
  const step = stageNumber(job?.processing_stage);
  const why = stageReason(job?.processing_stage);
  const failure = failed ? describeFailure(job) : null;
  const attempts = failed ? attemptsLabel(job) : null;

  return (
    <article
      className={cx(styles.row, ready && styles.ready, busy && styles.busy, failed && styles.failed)}
      aria-busy={entry.pending !== null}
    >
      <span className={styles.mark} aria-hidden="true">
        {ready ? <Check className={styles.markIcon} /> : null}
        {failed ? <AlertTriangle className={styles.markIcon} /> : null}
        {busy ? <Breath /> : null}
      </span>

      <p className={styles.name} title={document.original_file_name}>
        {document.original_file_name}
      </p>

      <span className="visually-hidden">{documentStatusLabel(document.status)}</span>

      {ready ? (
        <p className={styles.facts}>
          {readyFacts(entry).map((fact, index) => (
            <span key={fact}>
              {index > 0 ? <span className={styles.dot}>·</span> : null}
              <span className={index > 0 ? 'tabular' : undefined}>{fact}</span>
            </span>
          ))}
        </p>
      ) : null}

      {busy ? (
        <>
          <p className={styles.stage}>
            <span className={styles.stageName}>{stage ?? 'Waiting to start'}</span>
            {step !== null ? (
              <>
                <span className={styles.dot}>·</span>
                <span className="tabular">
                  step {step} of {TOTAL_STAGES}
                </span>
              </>
            ) : null}
          </p>
          <div
            className={styles.bar}
            role="progressbar"
            aria-valuenow={step ?? 0}
            aria-valuemin={0}
            aria-valuemax={TOTAL_STAGES}
            aria-label={`Reading ${document.original_file_name}`}
          >
            <div
              className={styles.barFill}
              style={{ width: `${((step ?? 0) / TOTAL_STAGES) * 100}%` }}
            />
          </div>
          {why ? <p className={styles.locked}>{why}</p> : null}
          <p className={styles.locked}>It cannot be removed until this finishes.</p>
        </>
      ) : null}

      {failure ? (
        <div className={styles.failure}>
          <p className={styles.failureHead}>
            <span className={styles.failureHeadline}>{failure.headline}</span>
            {attempts ? (
              <>
                <span className={styles.dot}>·</span>
                <span className="tabular">{attempts}</span>
              </>
            ) : null}
          </p>
          <p className={styles.reason}>{failure.what}</p>
          {failure.fix ? <p className={styles.fix}>{failure.fix}</p> : null}
        </div>
      ) : null}

      {entry.error ? (
        <p className={styles.reason} role="alert">
          {entry.error}
        </p>
      ) : null}

      {!busy ? (
        <div className={styles.actions}>
          {failed ? (
            <Button
              size="sm"
              onClick={() => onRetry(document.id)}
              disabled={entry.pending !== null}
              isLoading={entry.pending === 'retry'}
              loadingLabel="Trying again"
              icon={<RotateCcw aria-hidden="true" />}
            >
              Try again
            </Button>
          ) : null}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setIsConfirming(true)}
            disabled={entry.pending !== null}
            isLoading={entry.pending === 'delete'}
            loadingLabel="Removing"
            icon={<Trash2 aria-hidden="true" />}
            aria-label={`Remove ${document.original_file_name}`}
          >
            Remove
          </Button>
        </div>
      ) : null}

      <ConfirmDialog
        open={isConfirming}
        onClose={() => setIsConfirming(false)}
        onConfirm={() => {
          setIsConfirming(false);
          onDelete(document.id);
        }}
        title="Remove this source?"
        confirmLabel="Remove it"
        destructive
      >
        Everything built from {document.original_file_name} stays, but nothing new can draw on
        it. Putting it back means uploading and processing it again.
      </ConfirmDialog>
    </article>
  );
}
