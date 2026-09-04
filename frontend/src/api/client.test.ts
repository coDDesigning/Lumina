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
      message: 'Unsupported file type. Please upload a PDF, TXT, Markdown, or image (PNG or JPEG) file.',
      data: { code: 'UPLOAD_UNSUPPORTED_FILE_TYPE' },
    });

    expect(error.message).toBe(
      'Unsupported file type. Please upload a PDF, TXT, Markdown, or image (PNG or JPEG) file.',
    );
    expect(error.code).toBe('UPLOAD_UNSUPPORTED_FILE_TYPE');
  });

  it('falls back when the body is not a recognised shape', () => {
    expect(new APIError(500, null).message).toBe('An API error occurred');
    expect(new APIError(500, { unexpected: true }).message).toBe(
      'An API error occurred',
    );
  });

  it.each([
    ['0', 0],
    ['42', 42],
    [' 7 ', 7],
    [null, null],
    ['', null],
    ['-1', null],
    ['1.5', null],
    ['Wed, 21 Oct 2015 07:28:00 GMT', null],
    ['9007199254740992', null],
  ])('parses Retry-After %s as %s seconds', (retryAfter, expected) => {
    const error = new APIError(429, null, 'generation_rate_limited', retryAfter);

    expect(error.retryAfterSeconds).toBe(expected);
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

  it('distinguishes invalid data from a missing payload', () => {
    const error = new MalformedResponseError('Stored guide', 'invalid_data');

    expect(error.reason).toBe('invalid_data');
    expect(error.message).toBe('Stored guide returned invalid data.');
  });
});

describe('API base URL', () => {
  const originalFetch = global.fetch;

  async function requestWithBaseUrl(baseUrl: string | undefined, endpoint: string) {
    vi.stubEnv('VITE_API_BASE_URL', baseUrl);
    vi.resetModules();
    const { apiClient: configuredClient } = await import('./client');
    const mockFetch = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }));
    global.fetch = mockFetch;

    await configuredClient.get(endpoint);

    return mockFetch;
  }

  afterEach(() => {
    global.fetch = originalFetch;
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it.each([undefined, '', '   '])('defaults %s to /api', async (baseUrl) => {
    const mockFetch = await requestWithBaseUrl(baseUrl, '/courses/?page=2');

    expect(mockFetch).toHaveBeenCalledWith('/api/courses/?page=2', expect.any(Object));
  });

  it('normalizes a root-relative base while preserving endpoint syntax', async () => {
    const mockFetch = await requestWithBaseUrl('/gateway/api///', '///courses/?page=2');

    expect(mockFetch).toHaveBeenCalledWith(
      '/gateway/api/courses/?page=2',
      expect.any(Object),
    );
  });

  it.each([
    [
      'https://api.example.com/v1/',
      '/courses/?page=2',
      'https://api.example.com/v1/courses/?page=2',
    ],
    ['http://localhost:8000/api', 'health/', 'http://localhost:8000/api/health/'],
  ])('supports the absolute base %s', async (baseUrl, endpoint, expectedUrl) => {
    const mockFetch = await requestWithBaseUrl(baseUrl, endpoint);

    expect(mockFetch).toHaveBeenCalledWith(expectedUrl, expect.any(Object));
  });

  it.each([
    'api',
    './api',
    '../api',
    '//api.example.com/api',
    '/\\evil.example/api',
    '/\t/evil.example/api',
    '\t/api',
    'ftp://api.example.com/api',
    'https://user:password@api.example.com/api',
    'https://@api.example.com/api',
    'https://api.example.com/api?tenant=lumina',
    'https://api.example.com/api#resources',
  ])('rejects the invalid base %s', async (baseUrl) => {
    vi.stubEnv('VITE_API_BASE_URL', baseUrl);
    vi.resetModules();

    await expect(import('./client')).rejects.toThrow('VITE_API_BASE_URL');
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
    expect(mockFetch.mock.calls[0][1]).not.toHaveProperty('credentials');
    expect(result).toEqual({ success: true, message: 'ok', data: { id: 1 } });
  });

  it('reads the stable error code from the X-Error-Code header', async () => {
    const mockFetch = vi.mocked(global.fetch);
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'You do not have enough credits.' }), {
        status: 402,
        headers: {
          'Content-Type': 'application/json',
          'X-Error-Code': 'insufficient_credits',
        },
      }),
    );

    await expect(apiClient.post('/courses/1/quiz', {})).rejects.toMatchObject({
      status: 402,
      code: 'insufficient_credits',
      message: 'You do not have enough credits.',
    });
  });

  it('exposes Retry-After delta-seconds from an error response', async () => {
    const mockFetch = vi.mocked(global.fetch);
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'Too many generation requests.' }), {
        status: 429,
        headers: {
          'Content-Type': 'application/json',
          'X-Error-Code': 'generation_rate_limited',
          'Retry-After': '23',
        },
      }),
    );

    await expect(apiClient.post('/courses/1/quiz', {})).rejects.toMatchObject({
      status: 429,
      code: 'generation_rate_limited',
      retryAfterSeconds: 23,
    });
  });

  it('prefers a code carried in the response envelope over the header', async () => {
    const mockFetch = vi.mocked(global.fetch);
    mockFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          success: false,
          message: 'Upload blocked',
          data: { code: 'UPLOAD_DOCUMENT_DELETION_IN_PROGRESS' },
        }),
        {
          status: 409,
          headers: {
            'Content-Type': 'application/json',
            'X-Error-Code': 'something_else',
          },
        },
      ),
    );

    await expect(apiClient.post('/courses/1/documents', {})).rejects.toMatchObject({
      code: 'UPLOAD_DOCUMENT_DELETION_IN_PROGRESS',
    });
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

  it('keeps the Lumina session for a personal API key 401', async () => {
    localStorage.setItem('token', 'valid-lumina-token');
    const mockFetch = vi.mocked(global.fetch);
    mockFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ detail: 'Your personal OpenAI API key is invalid or expired.' }),
        {
          status: 401,
          headers: { 'X-Error-Code': 'personal_key_invalid' },
        },
      ),
    );
    const unauthorizedListener = vi.fn();
    window.addEventListener('auth:unauthorized', unauthorizedListener);

    await expect(apiClient.post('/courses/1/study-guide', {})).rejects.toMatchObject({
      status: 401,
      code: 'personal_key_invalid',
    });

    expect(localStorage.getItem('token')).toBe('valid-lumina-token');
    expect(unauthorizedListener).not.toHaveBeenCalled();
    window.removeEventListener('auth:unauthorized', unauthorizedListener);
  });
});
