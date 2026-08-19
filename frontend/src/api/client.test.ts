import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  apiClient,
  APIError,
  MalformedResponseError,
  unwrapData,
} from './client';

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
    expect(() =>
      // @ts-expect-error intentionally testing undefined payload
      unwrapData({ success: true, message: 'ok' }, 'Course'),
    ).toThrow(MalformedResponseError);
  });
});

describe('apiClient HTTP requests', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    localStorage.clear();
    global.fetch = vi.fn();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    localStorage.clear();
  });

  it('performs GET request with Authorization header if token exists', async () => {
    localStorage.setItem('token', 'saved-jwt');
    const mockFetch = vi.mocked(global.fetch);
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ success: true, message: 'ok', data: { id: 1 } }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    const result = await apiClient.get<{ data: { id: number } }>('/courses');

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/courses',
      expect.objectContaining({
        method: 'GET',
        headers: expect.any(Headers),
      }),
    );

    const headers = mockFetch.mock.calls[0][1]?.headers as Headers;
    expect(headers.get('Authorization')).toBe('Bearer saved-jwt');
    expect(result).toEqual({ success: true, message: 'ok', data: { id: 1 } });
  });

  it('performs POST request and stringifies json body', async () => {
    const mockFetch = vi.mocked(global.fetch);
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ id: 10 }), { status: 201 }),
    );

    await apiClient.post('/courses', { title: 'New Course' });

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/courses',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ title: 'New Course' }),
      }),
    );
  });

  it('performs PUT and DELETE requests', async () => {
    const mockFetch = vi.mocked(global.fetch);
    mockFetch.mockImplementation(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));

    await apiClient.put('/courses/1', { title: 'Updated' });
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/courses/1',
      expect.objectContaining({ method: 'PUT', body: JSON.stringify({ title: 'Updated' }) }),
    );

    await apiClient.delete('/courses/1');
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/courses/1',
      expect.objectContaining({ method: 'DELETE' }),
    );
  });

  it('performs postForm with URLSearchParams and FormData', async () => {
    const mockFetch = vi.mocked(global.fetch);
    mockFetch.mockImplementation(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));

    const params = new URLSearchParams({ username: 'test', password: 'pwd' });
    await apiClient.postForm('/auth/token', params);

    const paramsHeaders = mockFetch.mock.calls[0][1]?.headers as Headers;
    expect(paramsHeaders.get('Content-Type')).toBe('application/x-www-form-urlencoded');

    const form = new FormData();
    form.append('file', new Blob(['test']), 'doc.pdf');
    await apiClient.postForm('/courses/1/documents', form);

    const formHeaders = mockFetch.mock.calls[1][1]?.headers as Headers;
    expect(formHeaders.has('Content-Type')).toBe(false);
  });

  it('dispatches auth:unauthorized event and clears token on 401 response', async () => {
    localStorage.setItem('token', 'expired-token');
    const mockFetch = vi.mocked(global.fetch);
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'Token expired' }), {
        status: 401,
        statusText: 'Unauthorized',
      }),
    );

    const unauthorizedListener = vi.fn();
    window.addEventListener('auth:unauthorized', unauthorizedListener);

    await expect(apiClient.get('/user/me')).rejects.toThrow(APIError);

    expect(localStorage.getItem('token')).toBeNull();
    expect(unauthorizedListener).toHaveBeenCalledTimes(1);

    window.removeEventListener('auth:unauthorized', unauthorizedListener);
  });
});
