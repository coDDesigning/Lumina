import { useState } from 'react';
import { FileText, RotateCcw, Trash2 } from 'lucide-react';
import type { DocumentEntry } from '@/hooks/useCourseDocuments';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { ConfirmDialog } from '@/ui/ConfirmDialog';
import {
  documentStatusLabel,
  documentStatusTone,
  formatFileSize,
  isDocumentBusy,
  progressLabel,
} from './documentLabels';
import styles from './DocumentRow.module.css';

export interface DocumentRowProps {
  entry: DocumentEntry;
  onRetry: (documentId: string) => void;
  onDelete: (documentId: string) => void;
}

function describeEntry(entry: DocumentEntry): string {
  const { document, job } = entry;
  const stage = progressLabel(job);

  if (document.status === 'failed') {
    const reason = job?.last_error_message ?? 'Processing failed.';
    return stage ? `${stage} failed. ${reason}` : reason;
  }

  if (document.status === 'ready') {
    const size = formatFileSize(document.file_size);
    const type = document.file_type.toUpperCase();
    return size ? `${type} · ${size}` : type;
  }

  return stage ?? '';
}

export function DocumentRow({ entry, onRetry, onDelete }: DocumentRowProps) {
  const [isConfirming, setIsConfirming] = useState(false);
  const { document, pending } = entry;

  const busy = isDocumentBusy(document.status);
  const canRetry = document.status === 'failed';
  const description = describeEntry(entry);

  return (
    <article className={styles.row} aria-busy={pending !== null}>
      <FileText className={styles.icon} aria-hidden="true" />

      <div className={styles.body}>
        <p className={styles.name} title={document.original_file_name}>
          {document.original_file_name}
        </p>
        <p className={styles.meta}>
          <Badge tone={documentStatusTone(document.status)}>
            {documentStatusLabel(document.status)}
          </Badge>
          {description ? <span className={styles.description}>{description}</span> : null}
        </p>
        {busy ? (
          <p className={styles.locked}>A source cannot be removed while it is being read.</p>
        ) : null}
        {entry.error ? (
          <p className={styles.error} role="alert">
            {entry.error}
          </p>
        ) : null}
      </div>

      <div className={styles.actions}>
        {canRetry ? (
          <Button
            size="sm"
            onClick={() => onRetry(document.id)}
            disabled={pending !== null}
            isLoading={pending === 'retry'}
            loadingLabel="Trying again"
            icon={<RotateCcw aria-hidden="true" />}
          >
            Try again
          </Button>
        ) : null}

        <Button
          size="sm"
          variant="destructive"
          onClick={() => setIsConfirming(true)}
          disabled={pending !== null || busy}
          isLoading={pending === 'delete'}
          loadingLabel="Removing"
          icon={<Trash2 aria-hidden="true" />}
          aria-label={`Remove ${document.original_file_name}`}
        >
          Remove
        </Button>
      </div>

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
