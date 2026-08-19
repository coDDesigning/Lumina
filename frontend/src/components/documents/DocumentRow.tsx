import { useState } from 'react';
import { File, RotateCcw, Trash2 } from 'lucide-react';
import type { DocumentEntry } from '../../hooks/useCourseDocuments';
import {
  documentStatusLabel,
  documentStatusTone,
  formatFileSize,
  isDocumentBusy,
  progressLabel,
} from './documentLabels';
import './documents.css';

interface DocumentRowProps {
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

  if (document.status === 'uploaded') {
    return stage ?? 'Waiting to start';
  }

  if (document.status === 'processing') {
    return stage ?? 'Processing';
  }

  return stage ?? '';
}

export function DocumentRow({ entry, onRetry, onDelete }: DocumentRowProps) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const { document, pending } = entry;

  const busy = isDocumentBusy(document.status);
  const canRetry = document.status === 'failed';
  const description = describeEntry(entry);

  return (
    <article className="source-item" aria-busy={pending !== null}>
      <File aria-hidden="true" strokeWidth={2.1} />
      <div className="source-item-body">
        <h2 title={document.original_file_name}>{document.original_file_name}</h2>
        <p>
          <span className={`doc-badge is-${documentStatusTone(document.status)}`}>
            {documentStatusLabel(document.status)}
          </span>
          {description ? <span className="doc-description">{description}</span> : null}
        </p>
        {entry.error ? (
          <p className="doc-row-error" role="alert">
            {entry.error}
          </p>
        ) : null}
      </div>
      <div className="doc-actions">
        {canRetry ? (
          <button
            className="doc-action-button"
            type="button"
            onClick={() => onRetry(document.id)}
            disabled={pending !== null}
          >
            <RotateCcw aria-hidden="true" />
            {pending === 'retry' ? 'Retrying…' : 'Retry'}
          </button>
        ) : null}

        {confirmingDelete ? (
          <>
            <button
              className="doc-action-button"
              type="button"
              onClick={() => setConfirmingDelete(false)}
              disabled={pending !== null}
            >
              Cancel
            </button>
            <button
              className="doc-action-button is-danger"
              type="button"
              onClick={() => {
                setConfirmingDelete(false);
                onDelete(document.id);
              }}
              disabled={pending !== null}
            >
              {pending === 'delete' ? 'Removing…' : 'Remove'}
            </button>
          </>
        ) : (
          <button
            className="doc-action-button"
            type="button"
            onClick={() => setConfirmingDelete(true)}
            disabled={pending !== null || busy}
            title={
              busy ? 'A source cannot be removed while it is being processed.' : undefined
            }
            aria-label={`Delete ${document.original_file_name}`}
          >
            <Trash2 aria-hidden="true" />
            {pending === 'delete' ? 'Removing…' : 'Delete'}
          </button>
        )}
      </div>
    </article>
  );
}
