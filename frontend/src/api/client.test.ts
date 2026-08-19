import { describe, expect, it } from 'vitest';
import { APIError, MalformedResponseError, unwrapData } from './client';

describe('APIError', () => {
  it('reads a plain string detail', () => {
    const error = new APIError(409, {
      detail: 'The document cannot be deleted while it is being processed.',
    });

    expect(error.message).toBe(
      'The document cannot be deleted while it is being processed.',
    );
    expect(error.code).toBeNull();
    expect(error.status).toBe(409);
  });

  it('reads a pydantic validation detail array', () => {
    const error = new APIError(422, {
      detail: [{ loc: ['body', 'topic_focus'], msg: 'Field required' }],
    });

    expect(error.message).toBe('topic_focus: Field required');
    expect(error.code).toBeNull();
  });

  it('reads the curated upload envelope and exposes its code', () => {
    const error = new APIError(415, {
      success: false,
      message: 'Unsupported file type. Please upload a PDF, TXT, or Markdown file.',
      data: { code: 'UPLOAD_UNSUPPORTED_FILE_TYPE' },
    });

    expect(error.message).toBe(
      'Unsupported file type. Please upload a PDF, TXT, or Markdown file.',
    );
    expect(error.code).toBe('UPLOAD_UNSUPPORTED_FILE_TYPE');
  });

  it('falls back when the body is not a recognised shape', () => {
    expect(new APIError(500, null).message).toBe('An API error occurred');
    expect(new APIError(500, { unexpected: true }).message).toBe(
      'An API error occurred',
    );
  });
});

describe('unwrapData', () => {
  it('returns the payload of a successful envelope', () => {
    expect(unwrapData({ success: true, message: 'ok', data: { id: 1 } }, 'Thing')).toEqual(
      { id: 1 },
    );
  });

  it('throws when the envelope carries no data', () => {
    expect(() => unwrapData({ success: true, message: 'ok', data: null }, 'Thing')).toThrow(
      MalformedResponseError,
    );
  });
});
