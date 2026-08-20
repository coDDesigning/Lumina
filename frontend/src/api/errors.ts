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

export function describeGenerationError(
  error: unknown,
  fallback: string,
): DescribedError {
  const described = describeError(error, fallback);

  if (described.status === 400) {
    return {
      ...described,
      message: `${described.message} Add a source and wait until it shows Ready.`,
      retryable: false,
    };
  }

  if (described.status === 409) {
    // The course has material, it just did not match this request. The backend
    // message already says what to do, so the "add a source" advice would be wrong.
    return { ...described, retryable: false };
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
