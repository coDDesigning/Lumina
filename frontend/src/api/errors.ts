import { APIError } from './client';

export interface DescribedError {
  message: string;
  status: number | null;
  code: string | null;
  retryable: boolean;
}

const RETRYABLE_STATUSES = new Set([408, 429, 500, 502, 503, 504]);

export function isAbortError(error: unknown): boolean {
  if (error instanceof DOMException) {
    return error.name === 'AbortError';
  }
  return error instanceof Error && error.name === 'AbortError';
}

export function describeError(error: unknown, fallback: string): DescribedError {
  if (error instanceof APIError) {
    return {
      message: error.message || fallback,
      status: error.status,
      code: error.code,
      retryable: RETRYABLE_STATUSES.has(error.status),
    };
  }

  if (error instanceof TypeError) {
    return {
      message: 'Network error. Check your connection and try again.',
      status: null,
      code: null,
      retryable: true,
    };
  }

  return { message: fallback, status: null, code: null, retryable: false };
}

export function describeUploadError(error: unknown): DescribedError {
  const described = describeError(error, 'The file could not be uploaded.');

  if (described.code === 'UPLOAD_DOCUMENT_DELETION_IN_PROGRESS') {
    return { ...described, retryable: true };
  }

  if (described.status === 404) {
    return {
      ...described,
      message: 'This course is no longer available.',
      retryable: false,
    };
  }

  return described;
}

export function describeDocumentError(error: unknown, fallback: string): DescribedError {
  const described = describeError(error, fallback);

  if (described.status === 404) {
    return {
      ...described,
      message: 'This source is no longer available.',
      retryable: false,
    };
  }

  return described;
}

export const INSUFFICIENT_CREDITS_CODE = 'insufficient_credits';

/** True when the backend refused this request for want of credits. */
export function isInsufficientCredits(described: DescribedError): boolean {
  return described.status === 402 || described.code === INSUFFICIENT_CREDITS_CODE;
}

export type GenerationRemedy = 'broaden_topic' | 'add_source' | 'see_sources' | 'shorten' | null;

export interface GenerationFailure extends DescribedError {
  title: string;
  remedy: GenerationRemedy;
}

interface FailureCopy {
  title: string;
  message: string;
  retryable: boolean;
  remedy: GenerationRemedy;
}

const GENERATION_FAILURES: Record<string, FailureCopy> = {
  no_ready_material: {
    title: 'Nothing is ready yet',
    message:
      'Your sources are still being read. Once at least one of them is ready, this will work.',
    retryable: false,
    remedy: 'see_sources',
  },
  no_relevant_material: {
    title: 'Nothing on that topic',
    message:
      'Nothing in your material covers this. Try a broader topic, or upload the material that covers it.',
    retryable: false,
    remedy: 'broaden_topic',
  },
  material_not_indexed: {
    title: 'Your material is not searchable yet',
    message:
      'Your sources are ready but have not been indexed yet. This one is on our side, not yours.',
    retryable: true,
    remedy: null,
  },
  source_not_ready: {
    title: 'A chosen source is still being read',
    message:
      'One of the documents you selected has not finished processing. Nothing was charged — try again once it is ready.',
    retryable: true,
    remedy: 'see_sources',
  },
  exam_date_missing: {
    title: 'This course has no exam date',
    message: 'Set an exam date for the course, then create the plan.',
    retryable: false,
    remedy: null,
  },
  exam_date_not_future: {
    title: 'That exam date has passed',
    message:
      'A first plan needs an exam still to come. Update the course exam date, then try again.',
    retryable: false,
    remedy: null,
  },
  exam_analysis_required: {
    title: 'Analyse your sources first',
    message: 'Run the topic analysis for this course before creating an exam plan.',
    retryable: false,
    remedy: 'see_sources',
  },
  exam_topic_selection_required: {
    title: 'Choose what to study',
    message: 'Select at least one topic before creating the plan.',
    retryable: false,
    remedy: null,
  },
  exam_topic_not_discovered: {
    title: 'That topic is not in this analysis',
    message:
      'One of the selected topics is not part of the analysis it was chosen from. Review the discovered topics and try again.',
    retryable: false,
    remedy: null,
  },
  retrieval_unavailable: {
    title: 'Search is unavailable',
    message: 'Your material could not be searched just now. Nothing was charged.',
    retryable: true,
    remedy: null,
  },
  provider_unavailable: {
    title: 'The AI service is down',
    message: 'The model could not be reached. Nothing was charged.',
    retryable: true,
    remedy: null,
  },
  provider_timeout: {
    title: 'That took too long',
    message:
      'The model did not answer in time. Asking for something shorter, or narrowing the topic, usually goes through.',
    retryable: true,
    remedy: 'shorten',
  },
  provider_rate_limited: {
    title: 'Too many requests right now',
    message: 'The model is busy. Try again in about a minute — nothing was charged.',
    retryable: true,
    remedy: null,
  },
  invalid_generated_structure: {
    title: 'The answer came back unreadable',
    message:
      'The model returned something that could not be read, so nothing was saved and your credit was refunded.',
    retryable: true,
    remedy: null,
  },
  generation_failed: {
    title: 'That did not work',
    message: 'The request could not be completed. Nothing was saved.',
    retryable: true,
    remedy: null,
  },
};

export function describeGenerationError(error: unknown, fallback: string): GenerationFailure {
  const described = describeError(error, fallback);
  const known = described.code ? GENERATION_FAILURES[described.code] : undefined;

  if (known) {
    return {
      ...described,
      title: known.title,
      message: known.message,
      retryable: known.retryable,
      remedy: known.remedy,
    };
  }

  if (isInsufficientCredits(described)) {
    return { ...described, title: 'Not enough credits', retryable: false, remedy: null };
  }

  if (described.status === 404) {
    return {
      ...described,
      title: 'This course is gone',
      message: 'It may have been deleted, or the link may belong to someone else.',
      retryable: false,
      remedy: null,
    };
  }

  if (described.status === null) {
    return {
      ...described,
      title: 'You are offline',
      retryable: true,
      remedy: null,
    };
  }

  return { ...described, title: 'That did not work', remedy: null };
}
