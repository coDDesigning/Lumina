import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MalformedResponseError } from './client';
import { generatedOutputsAPI } from './generatedOutputs';
import type { GeneratedOutputDetail, GeneratedOutputSummary } from './types';

const SUMMARY: GeneratedOutputSummary = {
  id: 12,
  course_id: 7,
  output_type: 'study_guide',
  user_id: 3,
  model_used: 'ollama:qwen3:8b',
  created_at: '2026-08-20T10:00:00Z',
  generation_settings: {
    version: 1,
    output_type: 'study_guide',
    summary_format: 'exam_tips',
    topic_focus: 'Graphs',
    summary_length: 'long',
    detail_level: 'detailed',
    summary_mode: 'exam_focused',
    retrieval_limit: 24,
    retrieval_min_similarity: 0.25,
  },
  generation_context: {
    version: 1,
    chunks_ranked: 24,
    chunks_retrieved: 18,
    chunks_used: 18,
    chunks_available: 200,
    lowest_similarity: 0.41,
    highest_similarity: 0.88,
    truncated: false,
  },
};

const DETAIL: GeneratedOutputDetail = {
  ...SUMMARY,
  content: { title: 'Stored Guide' },
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

describe('generatedOutputsAPI', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token');
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('lists a course history and unwraps the BaseResponse envelope', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: [SUMMARY] }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await generatedOutputsAPI.list(7);

    expect(result).toEqual([SUMMARY]);

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/courses/7/generated-outputs');
    expect(init?.method ?? 'GET').toBe('GET');
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer test-token');
  });

  it('accepts an empty history without treating it as malformed', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: [] }),
      ),
    );

    await expect(generatedOutputsAPI.list(7)).resolves.toEqual([]);
  });

  it('fetches one stored output by its identifier', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: DETAIL }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await generatedOutputsAPI.get(7, 12);

    expect(result).toEqual(DETAIL);
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      '/api/courses/7/generated-outputs/12',
    );
  });

  it('rejects when the envelope carries no data', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: null }),
      ),
    );

    await expect(generatedOutputsAPI.get(7, 12)).rejects.toBeInstanceOf(
      MalformedResponseError,
    );
  });
});
