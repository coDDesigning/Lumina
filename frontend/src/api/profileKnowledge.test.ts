import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MalformedResponseError } from './client';
import { profileKnowledgeAPI } from './profileKnowledge';
import type { ProfileKnowledgeItem } from './types';

const SAMPLE_ITEM: ProfileKnowledgeItem = {
  id: 1,
  user_id: 10,
  topic: 'Machine Learning',
  detail: 'Knows gradient descent and backpropagation.',
  created_at: '2026-08-19T10:00:00Z',
  updated_at: '2026-08-19T10:00:00Z',
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

describe('profileKnowledgeAPI', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token');
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('lists knowledge items for the current user', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: [SAMPLE_ITEM] }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await profileKnowledgeAPI.list();
    expect(result).toEqual([SAMPLE_ITEM]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/profile-knowledge/');
    expect(init?.method).toBe('GET');
  });

  it('creates a new knowledge item', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'created', data: SAMPLE_ITEM }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await profileKnowledgeAPI.create({
      topic: 'Machine Learning',
      detail: 'Knows gradient descent and backpropagation.',
    });

    expect(result).toEqual(SAMPLE_ITEM);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/profile-knowledge/');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(init?.body as string)).toEqual({
      topic: 'Machine Learning',
      detail: 'Knows gradient descent and backpropagation.',
    });
  });

  it('updates an existing knowledge item', async () => {
    const updated = { ...SAMPLE_ITEM, topic: 'Advanced ML' };
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'updated', data: updated }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await profileKnowledgeAPI.update(1, { topic: 'Advanced ML' });
    expect(result.topic).toBe('Advanced ML');
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/profile-knowledge/1');
    expect(init?.method).toBe('PUT');
  });

  it('deletes a knowledge item', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'deleted', data: { id: 1 } }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await profileKnowledgeAPI.delete(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/profile-knowledge/1');
    expect(init?.method).toBe('DELETE');
  });

  it('bulk imports knowledge items', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'imported', data: [SAMPLE_ITEM] }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await profileKnowledgeAPI.importBulk({
      items: [{ topic: 'Machine Learning', detail: 'Knows gradient descent.' }],
    });
    expect(result).toEqual([SAMPLE_ITEM]);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/profile-knowledge/import');
    expect(init?.method).toBe('POST');
  });

  it('throws MalformedResponseError when data is missing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: null }),
      ),
    );

    await expect(profileKnowledgeAPI.get(1)).rejects.toBeInstanceOf(
      MalformedResponseError,
    );
  });
});
