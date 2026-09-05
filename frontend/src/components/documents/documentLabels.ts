import type {
  DocumentMaterialKind,
  DocumentStatus,
  DocumentVisualAnalysisStatus,
  ProcessingJobResponse,
  ProcessingStage,
} from '../../api/types';

import type { BadgeTone } from '@/ui/Badge';

export type { BadgeTone };

export const VISUAL_STATUS_LABELS: Record<DocumentVisualAnalysisStatus, string> = {
  not_applicable: '',
  pending: 'Analyzing visuals',
  not_configured: 'Visual analysis disabled',
  completed: 'Visuals indexed',
  partial: 'Partial visuals',
  failed: 'Visual analysis failed',
};

export function visualAnalysisStatusLabel(
  status: string | null | undefined,
): string | null {
  if (!status || status === 'not_applicable') {
    return null;
  }
  return VISUAL_STATUS_LABELS[status as DocumentVisualAnalysisStatus] ?? humanizeToken(status);
}

const STATUS_LABELS: Record<DocumentStatus, string> = {
  uploaded: 'Queued',
  processing: 'Processing',
  ready: 'Ready',
  failed: 'Failed',
  deleting: 'Removing',
};

const STATUS_TONES: Record<DocumentStatus, BadgeTone> = {
  uploaded: 'neutral',
  processing: 'processing',
  ready: 'success',
  failed: 'destructive',
  deleting: 'warning',
};

const STAGE_LABELS: Record<ProcessingStage, string> = {
  validating: 'Checking the file',
  extracting_text: 'Pulling out the text',
  running_ocr: 'Reading the scans',
  understanding_images: 'Describing the figures',
  cleaning_text: 'Tidying it up',
  chunking: 'Splitting it up',
  generating_embeddings: 'Indexing it',
};

const STAGE_REASONS: Partial<Record<ProcessingStage, string>> = {
  running_ocr: 'Some pages are images, so the text is being read off them.',
  understanding_images: 'There are diagrams here, so they are being described in words.',
};

export interface DocumentFailure {
  headline: string;
  what: string;
  fix: string | null;
}

const FAILURES: Record<string, DocumentFailure> = {
  PASSWORD_PROTECTED_PDF: {
    headline: 'Locked file',
    what: 'This PDF is password protected, so it cannot be opened.',
    fix: 'Save an unlocked copy and upload that instead.',
  },
  CORRUPTED_PDF: {
    headline: 'Damaged file',
    what: 'This PDF could not be opened. Part of the file looks damaged.',
    fix: 'Try downloading it again from wherever it came from.',
  },
  CORRUPTED_TEXT: {
    headline: 'Unreadable text',
    what: 'The text in this file is not in an encoding that could be read.',
    fix: 'Re-saving it as UTF-8, or as a PDF, usually works.',
  },
  NO_PROCESSABLE_TEXT: {
    headline: "Couldn't read it",
    what: 'No readable text was found, even after trying to read the pages as images.',
    fix: 'A clearer scan, or a photo taken straight on in good light, usually works.',
  },
  DOCUMENT_TOO_COMPLEX: {
    headline: 'Too much at once',
    what: 'This file has more going on in it than can be read in one pass.',
    fix: 'Splitting it into a few smaller uploads works better.',
  },
  EXTRACTED_TEXT_LIMIT_EXCEEDED: {
    headline: 'Too long',
    what: 'There is more text in this file than one source can hold.',
    fix: 'Upload it in parts — by chapter or by week.',
  },
  DOCUMENT_CHUNK_LIMIT_EXCEEDED: {
    headline: 'Too long',
    what: 'This file breaks down into more pieces than one source can hold.',
    fix: 'Upload it in parts — by chapter or by week.',
  },
  UNSUPPORTED_FILE_TYPE: {
    headline: 'Not a supported file',
    what: 'This kind of file cannot be read.',
    fix: 'PDF, plain text, Markdown, and PNG or JPEG images all work.',
  },
  DOCUMENT_TOO_LARGE: {
    headline: 'Too big',
    what: 'This file is larger than the upload limit.',
    fix: 'Upload it in parts, or export a smaller version.',
  },
  OCR_UNAVAILABLE: {
    headline: 'Scans cannot be read right now',
    what: 'This file needs its pages read as images, and that is unavailable at the moment.',
    fix: null,
  },
};

const GENERIC_FAILURE: DocumentFailure = {
  headline: "Couldn't read it",
  what: 'Something went wrong while reading this file.',
  fix: null,
};

export function stageReason(stage: string | null | undefined): string | null {
  if (!stage) {
    return null;
  }
  return STAGE_REASONS[stage as ProcessingStage] ?? null;
}

export function describeFailure(job: ProcessingJobResponse | null): DocumentFailure {
  const code = job?.last_error_code?.toUpperCase();
  const known = code ? FAILURES[code] : undefined;
  if (known) {
    return known;
  }
  const message = job?.last_error_message;
  return message ? { ...GENERIC_FAILURE, what: message } : GENERIC_FAILURE;
}

export function attemptsLabel(job: ProcessingJobResponse | null): string | null {
  if (!job || job.max_attempts <= 1 || job.attempt_count < 1) {
    return null;
  }
  return `attempt ${Math.min(job.attempt_count, job.max_attempts)} of ${job.max_attempts}`;
}

const MATERIAL_KIND_LABELS: Record<DocumentMaterialKind, string> = {
  lecture_notes: 'Lecture notes',
  slides: 'Slides',
  textbook: 'Textbook',
  syllabus: 'Syllabus',
  assignment: 'Assignment',
  past_exam: 'Past exam',
  article: 'Article',
  notes: 'Notes',
  other: 'Other',
  unspecified: '',
};

export const MATERIAL_KIND_CHOICES: { value: DocumentMaterialKind; label: string }[] = [
  { value: 'unspecified', label: "I'd rather not say" },
  { value: 'lecture_notes', label: 'Lecture notes' },
  { value: 'slides', label: 'Slides' },
  { value: 'textbook', label: 'Textbook' },
  { value: 'syllabus', label: 'Syllabus' },
  { value: 'assignment', label: 'Assignment' },
  { value: 'past_exam', label: 'Past exam' },
  { value: 'article', label: 'Article' },
  { value: 'notes', label: 'My own notes' },
  { value: 'other', label: 'Something else' },
];

const ORDERED_STAGES: ProcessingStage[] = [
  'validating',
  'extracting_text',
  'running_ocr',
  'understanding_images',
  'cleaning_text',
  'chunking',
  'generating_embeddings',
];

export const TOTAL_STAGES = ORDERED_STAGES.length;

export function materialKindLabel(kind: string | null | undefined): string {
  if (!kind) {
    return '';
  }
  return MATERIAL_KIND_LABELS[kind as DocumentMaterialKind] ?? humanizeToken(kind);
}

export function stageNumber(stage: string | null | undefined): number | null {
  if (!stage) {
    return null;
  }
  const index = ORDERED_STAGES.indexOf(stage as ProcessingStage);
  return index === -1 ? null : index + 1;
}

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

const FILE_NAME_DISPLAY_MAX = 60;

/**
 * A client-supplied filename as it should read in ordinary UI copy: no path
 * segments (a name like `../../etc/x.pdf` renders as broken prose otherwise),
 * no control characters, length-capped with an ellipsis. This is display-only
 * and deliberately separate from the citation-facing `document_label` on the
 * server, which is tuned for model-facing text.
 */
export function displayFileName(name: string, maxLength = FILE_NAME_DISPLAY_MAX): string {
  let cleaned = '';
  for (const ch of name) {
    const code = ch.codePointAt(0) ?? 0;
    // control chars, DEL, forward slash (0x2f), backslash (0x5c)
    if (code < 0x20 || code === 0x7f || code === 0x2f || code === 0x5c) {
      cleaned += ' ';
    } else {
      cleaned += ch;
    }
  }
  cleaned = cleaned.replace(/\.{2,}/g, ' ').replace(/\s+/g, ' ').trim();
  if (!cleaned) return 'file';
  if (cleaned.length <= maxLength) return cleaned;
  return `${cleaned.slice(0, Math.max(1, maxLength - 1)).trimEnd()}…`;
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
