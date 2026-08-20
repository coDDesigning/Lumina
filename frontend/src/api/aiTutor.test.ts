import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { aiTutorAPI } from './aiTutor';
import { MalformedResponseError } from './client';
import type { AiTutorGenerationResult } from './types';

const TUTOR_RESULT: AiTutorGenerationResult = {
  context_truncated: false,
  chunks_used: 3,
  chunks_available: 6,
  answer: 'Great question! Let us break down recursion into base cases and recursive steps.',
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

describe('aiTutorAPI.ask', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token');
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('posts the question and unwraps the tutor response', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: TUTOR_RESULT }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await aiTutorAPI.ask(42, {
      question: 'Can you teach me recursion step by step?',
      model: 'gemini:gemini-3.6-flash',
    });

    expect(result).toEqual(TUTOR_RESULT);
    expect(result.answer).toContain('base cases and recursive steps');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/courses/42/ai-tutor');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(init?.body as string)).toEqual({
      question: 'Can you teach me recursion step by step?',
      model: 'gemini:gemini-3.6-flash',
    });
    expect(new Headers(init?.headers).get('Authorization')).toBe(
      'Bearer test-token',
    );
  });

  it('rejects when envelope carries no data', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: null }),
      ),
    );

    await expect(
      aiTutorAPI.ask(42, { question: 'Teach me' }),
    ).rejects.toBeInstanceOf(MalformedResponseError);
  });
});
