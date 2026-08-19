import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MalformedResponseError } from './client';
import { courseQaAPI } from './courseQa';
import type { CourseQAGenerationResult } from './types';

const QA_RESULT: CourseQAGenerationResult = {
  context_truncated: false,
  chunks_used: 2,
  chunks_available: 5,
  answer: 'Mitochondria produce ATP through cellular respiration.',
};

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: 'OK',
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as Response;
}

describe('courseQaAPI.ask', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token');
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('posts the question and unwraps the BaseResponse envelope', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: QA_RESULT }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await courseQaAPI.ask(12, {
      question: 'What is the function of mitochondria?',
    });

    expect(result).toEqual(QA_RESULT);
    expect(result.answer).toBe(
      'Mitochondria produce ATP through cellular respiration.',
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/courses/12/qa');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(init?.body as string)).toEqual({
      question: 'What is the function of mitochondria?',
    });
    expect(new Headers(init?.headers).get('Authorization')).toBe(
      'Bearer test-token',
    );
  });

  it('rejects when the envelope carries no data', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: null }),
      ),
    );

    await expect(
      courseQaAPI.ask(12, { question: 'Some question' }),
    ).rejects.toBeInstanceOf(MalformedResponseError);
  });
});
