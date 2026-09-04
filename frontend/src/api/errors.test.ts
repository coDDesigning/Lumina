import { describe, expect, it } from 'vitest';
import { APIError, MalformedResponseError } from './client';
import {
  describeDocumentError,
  describeError,
  describeGenerationError,
  describeUploadError,
  INVALID_RESPONSE_DATA_CODE,
  isAbortError,
  isInsufficientCredits,
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

  describe('insufficient credits', () => {
    it('recognises a 402 even when the server sent no error code', () => {
      const described = describeGenerationError(
        new APIError(402, { detail: 'You do not have enough credits.' }),
        'Fallback',
      );
      expect(isInsufficientCredits(described)).toBe(true);
      expect(described.message).toBe('You do not have enough credits.');
    });

    it('recognises the stable code from the X-Error-Code header', () => {
      const described = describeGenerationError(
        new APIError(402, { detail: 'Nope' }, 'insufficient_credits'),
        'Fallback',
      );
      expect(described.code).toBe('insufficient_credits');
      expect(isInsufficientCredits(described)).toBe(true);
    });

    it('is never retryable, because retrying cannot create credits', () => {
      const described = describeGenerationError(
        new APIError(402, { detail: 'Out of credits' }),
        'Fallback',
      );
      expect(described.retryable).toBe(false);
    });

    it('does not add the "add a source" advice meant for an empty course', () => {
      const described = describeGenerationError(
        new APIError(402, { detail: 'Out of credits' }),
        'Fallback',
      );
      expect(described.message).not.toContain('Add a source');
    });

    it('leaves other generation failures alone', () => {
      const described = describeGenerationError(
        new APIError(409, { detail: 'No material matched.' }),
        'Fallback',
      );
      expect(isInsufficientCredits(described)).toBe(false);
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

    it('preserves an invalid response as a typed client failure', () => {
      const described = describeError(
        new MalformedResponseError('Exam topic guide', 'invalid_data'),
        'Fallback',
      );

      expect(described).toEqual({
        message: 'Exam topic guide returned invalid data.',
        status: null,
        code: INVALID_RESPONSE_DATA_CODE,
        retryable: false,
      });
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
        'Unsupported file type. Please upload a PDF, TXT, Markdown, or image (PNG or JPEG) file.',
      );
      const described = describeUploadError(err);
      expect(described.status).toBe(415);
      expect(described.code).toBe('UPLOAD_UNSUPPORTED_FILE_TYPE');
      expect(described.message).toBe(
        'Unsupported file type. Please upload a PDF, TXT, Markdown, or image (PNG or JPEG) file.',
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

    it('tells the two failures that share a 409 apart', () => {
      const missed = describeGenerationError(
        new APIError(409, { detail: 'No course material matched.' }, 'no_relevant_material'),
        'Generation failed',
      );
      const unindexed = describeGenerationError(
        new APIError(409, { detail: 'Not searchable yet.' }, 'material_not_indexed'),
        'Generation failed',
      );

      expect(missed.title).not.toBe(unindexed.title);
      expect(missed.message).not.toBe(unindexed.message);
      expect(missed.remedy).toBe('broaden_topic');
      expect(unindexed.remedy).toBeNull();
    });

    it('lets a reader retry an indexing failure but not a relevance miss', () => {
      const missed = describeGenerationError(
        new APIError(409, { detail: 'No match.' }, 'no_relevant_material'),
        'Generation failed',
      );
      const unindexed = describeGenerationError(
        new APIError(409, { detail: 'Not searchable yet.' }, 'material_not_indexed'),
        'Generation failed',
      );

      expect(missed.retryable).toBe(false);
      expect(unindexed.retryable).toBe(true);
    });

    it('points someone with nothing processed at their sources', () => {
      const described = describeGenerationError(
        new APIError(400, { detail: 'Course has no ready material.' }, 'no_ready_material'),
        'Generation failed',
      );

      expect(described.title).toBe('Nothing is ready yet');
      expect(described.remedy).toBe('see_sources');
      expect(described.retryable).toBe(false);
    });

    it('says a refund happened when the model returned something unreadable', () => {
      const described = describeGenerationError(
        new APIError(502, { detail: 'Bad structure.' }, 'invalid_generated_structure'),
        'Generation failed',
      );

      expect(described.message).toMatch(/refunded/i);
      expect(described.retryable).toBe(true);
    });

    it('says nothing was charged when the provider could not be reached', () => {
      const described = describeGenerationError(
        new APIError(503, { detail: 'Unavailable.' }, 'provider_unavailable'),
        'Generation failed',
      );

      expect(described.message).toMatch(/nothing was charged/i);
      expect(described.retryable).toBe(true);
    });

    it('describes the public generation throttle separately from provider throttling', () => {
      const generationThrottle = describeGenerationError(
        new APIError(
          429,
          { detail: 'Too many generation requests.' },
          'generation_rate_limited',
          '45',
        ),
        'Generation failed',
      );
      const providerThrottle = describeGenerationError(
        new APIError(429, { detail: 'Provider throttled.' }, 'provider_rate_limited'),
        'Generation failed',
      );

      expect(generationThrottle.title).not.toBe(providerThrottle.title);
      expect(generationThrottle.message).toBe(
        'Too many generation requests were made. Try again in 45 seconds. No credit was charged.',
      );
      expect(generationThrottle.message).toMatch(/no credit was charged/i);
      expect(generationThrottle.retryable).toBe(true);
      expect(providerThrottle.message).toMatch(/model is busy/i);
    });

    it('uses truthful fallback copy when the generation retry delay is unavailable', () => {
      const described = describeGenerationError(
        new APIError(429, { detail: 'Throttled.' }, 'generation_rate_limited', 'invalid'),
        'Generation failed',
      );

      expect(described.message).toBe(
        'Too many generation requests were made. Try again shortly. No credit was charged.',
      );
    });

    it('describes personal key invalid errors clearly with server message', () => {
      const described = describeGenerationError(
        new APIError(
          400,
          { detail: 'Your personal OpenAI API key is invalid or expired.' },
          'personal_key_invalid',
        ),
        'Generation failed',
      );

      expect(described.title).toBe('Invalid API key');
      expect(described.message).toBe('Your personal OpenAI API key is invalid or expired.');
      expect(described.retryable).toBe(false);
    });

    it('keeps whatever the server said for a code it has never seen', () => {
      const described = describeGenerationError(
        new APIError(400, { detail: 'Course has no ready material.' }, 'brand_new_code'),
        'Generation failed',
      );

      expect(described.message).toBe('Course has no ready material.');
      expect(described.title).toBe('That did not work');
    });
  });
});
