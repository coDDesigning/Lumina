import type {
  DocumentStatus,
  ProcessingJobResponse,
  ProcessingStage,
} from '../../api/types';

export type BadgeTone = 'neutral' | 'info' | 'success' | 'danger';

const STATUS_LABELS: Record<DocumentStatus, string> = {
  uploaded: 'Queued',
  processing: 'Processing',
  ready: 'Ready',
  failed: 'Failed',
  deleting: 'Removing',
};

const STATUS_TONES: Record<DocumentStatus, BadgeTone> = {
  uploaded: 'neutral',
  processing: 'info',
  ready: 'success',
  failed: 'danger',
  deleting: 'neutral',
};

const STAGE_LABELS: Record<ProcessingStage, string> = {
  validating: 'Validating document',
  extracting_text: 'Extracting text',
  running_ocr: 'Reading scanned pages',
  understanding_images: 'Analyzing diagrams and images',
  cleaning_text: 'Preparing document text',
  chunking: 'Preparing document for study',
  generating_embeddings: 'Indexing document for study',
};

const TERMINAL_DOCUMENT_STATUSES: ReadonlySet<string> = new Set(['ready', 'failed']);

const BUSY_DOCUMENT_STATUSES: ReadonlySet<string> = new Set([
  'uploaded',
  'processing',
  'deleting',
]);

export function humanizeToken(token: string): string {
  const spaced = token.replace(/[_-]+/g, ' ').trim();
  if (!spaced) return '';
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function documentStatusLabel(status: string): string {
  return STATUS_LABELS[status as DocumentStatus] ?? humanizeToken(status);
}

export function documentStatusTone(status: string): BadgeTone {
  return STATUS_TONES[status as DocumentStatus] ?? 'neutral';
}

export function processingStageLabel(stage: string | null): string | null {
  if (!stage) return null;
  return STAGE_LABELS[stage as ProcessingStage] ?? humanizeToken(stage);
}

export function isTerminalDocumentStatus(status: string): boolean {
  return TERMINAL_DOCUMENT_STATUSES.has(status);
}

export function isDocumentBusy(status: string): boolean {
  return BUSY_DOCUMENT_STATUSES.has(status);
}

export function progressLabel(job: ProcessingJobResponse | null): string | null {
  if (!job) return null;
  if (job.status === 'running') return processingStageLabel(job.processing_stage);
  if (job.status === 'failed') return processingStageLabel(job.failed_stage);
  return null;
}

export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
