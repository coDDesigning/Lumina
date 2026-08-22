import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MalformedResponseError } from './client';
import { conversationsAPI } from './conversations';
import type { ConversationDetail, ConversationSummary } from './types';

const SUMMARY: ConversationSummary = {
  id: 17,
  course_id: 4,
  user_id: 9,
  conversation_type: 'ai_tutor',
  preview: 'Explain recursion.',
  message_count: 2,
  created_at: '2026-08-20T10:00:00Z',
  updated_at: '2026-08-20T10:05:00Z',
};

const DETAIL: ConversationDetail = {
  ...SUMMARY,
  messages: [
    {
      id: 31,
      role: 'user',
      content: 'Explain recursion.',
      created_at: '2026-08-20T10:00:00Z',
    },
    {
      id: 32,
      role: 'assistant',
      content: 'Recursion solves a problem using smaller versions of itself.',
      created_at: '2026-08-20T10:00:01Z',
    },
  ],
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

describe('conversationsAPI', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token');
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('lists typed conversations for a course', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: [SUMMARY] }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(conversationsAPI.list(4)).resolves.toEqual([SUMMARY]);

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/courses/4/conversations');
    expect(init?.method).toBe('GET');
  });

  it('loads one conversation with its messages', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: DETAIL }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(conversationsAPI.get(4, 17)).resolves.toEqual(DETAIL);

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/courses/4/conversations/17');
    expect(init?.method).toBe('GET');
  });

  it('rejects a detail envelope without data', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: null }),
      ),
    );

    await expect(conversationsAPI.get(4, 17)).rejects.toBeInstanceOf(
      MalformedResponseError,
    );
  });

  it('deletes a conversation', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: { id: 17 } }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(conversationsAPI.delete(4, 17)).resolves.toEqual({ id: 17 });

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/courses/4/conversations/17');
    expect(init?.method).toBe('DELETE');
  });
});
