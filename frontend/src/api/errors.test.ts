import { describe, expect, it } from 'vitest';
import { APIError } from './client';
import {
  describeDocumentError,
  describeError,
  describeGenerationError,
  describeUploadError,
  isAbortError,
} from './errors';
import { MockErrors } from '../test/mocks/api';

describe('error description helpers', () => {
  describe('isAbortError', () => {
    it('identifies DOMException AbortError', () => {
      const abortError = new DOMException('The user aborted a request.', 'AbortError');
      expect(isAbortError(abortError)).toBe(true);
    });

    it('identifies standard Error with AbortError name', () => {
      const error = new Error('Aborted');
      error.name = 'AbortError';
      expect(isAbortError(error)).toBe(true);
    });

    it('returns false for regular errors', () => {
      expect(isAbortError(new Error('Network error'))).toBe(false);
      expect(isAbortError(null)).toBe(false);
    });
  });

  describe('describeError', () => {
    it('formats plain APIError', () => {
      const err = new APIError(500, { detail: 'Internal server error' });
      const described = describeError(err, 'Fallback');
      expect(described.message).toBe('Internal server error');
      expect(described.status).toBe(500);
      expect(described.retryable).toBe(true);
    });

    it('identifies retryable HTTP statuses (429, 500, 502, 503, 504)', () => {
      expect(describeError(new APIError(429, null), 'Fallback').retryable).toBe(true);
      expect(describeError(new APIError(503, null), 'Fallback').retryable).toBe(true);
      expect(describeError(new APIError(400, null), 'Fallback').retryable).toBe(false);
      expect(describeError(new APIError(404, null), 'Fallback').retryable).toBe(false);
    });

    it('handles network TypeError gracefully', () => {
      const typeError = new TypeError('Failed to fetch');
      const described = describeError(typeError, 'Fallback');
      expect(described.message).toBe(
        'Network error. Check your connection and try again.',
      );
      expect(described.retryable).toBe(true);
    });

    it('falls back for unknown errors', () => {
      const described = describeError('unexpected string', 'Default message');
      expect(described.message).toBe('Default message');
      expect(described.retryable).toBe(false);
    });
  });

  describe('describeUploadError', () => {
    it('handles 409 deletion in progress and marks retryable', () => {
      const err = MockErrors.conflict(
        'The document cannot be deleted while it is being processed.',
        'UPLOAD_DOCUMENT_DELETION_IN_PROGRESS',
      );
      const described = describeUploadError(err);
      expect(described.code).toBe('UPLOAD_DOCUMENT_DELETION_IN_PROGRESS');
      expect(described.retryable).toBe(true);
      expect(described.message).toBe(
        'The document cannot be deleted while it is being processed.',
      );
    });

    it('handles 413 file too large', () => {
      const err = MockErrors.payloadTooLarge(
        'The file exceeds the maximum allowed upload size of 50 MB.',
      );
      const described = describeUploadError(err);
      expect(described.status).toBe(413);
      expect(described.code).toBe('UPLOAD_FILE_TOO_LARGE');
      expect(described.message).toBe(
        'The file exceeds the maximum allowed upload size of 50 MB.',
      );
    });

    it('handles 415 unsupported file type', () => {
      const err = MockErrors.unsupportedMediaType(
        'Unsupported file type. Please upload a PDF, TXT, or Markdown file.',
      );
      const described = describeUploadError(err);
      expect(described.status).toBe(415);
      expect(described.code).toBe('UPLOAD_UNSUPPORTED_FILE_TYPE');
      expect(described.message).toBe(
        'Unsupported file type. Please upload a PDF, TXT, or Markdown file.',
      );
    });

    it('handles 422 pydantic validation errors', () => {
      const err = MockErrors.validation([
        { loc: ['body', 'file'], msg: 'Invalid file format' },
      ]);
      const described = describeUploadError(err);
      expect(described.status).toBe(422);
      expect(described.message).toBe('file: Invalid file format');
    });

    it('handles 404 course not found', () => {
      const err = MockErrors.notFound('Course 999 not found');
      const described = describeUploadError(err);
      expect(described.status).toBe(404);
      expect(described.message).toBe('This course is no longer available.');
      expect(described.retryable).toBe(false);
    });
  });

  describe('describeDocumentError and describeGenerationError', () => {
    it('formats 404 for missing documents', () => {
      const described = describeDocumentError(
        MockErrors.notFound(),
        'Could not load document',
      );
      expect(described.message).toBe('This source is no longer available.');
    });

    it('formats 400 for generation errors when material is needed', () => {
      const described = describeGenerationError(
        new APIError(400, { detail: 'Course has no ready material.' }),
        'Generation failed',
      );
      expect(described.message).toBe(
        'Course has no ready material. Add a source and wait until it shows Ready.',
      );
    });
  });
});
