import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MalformedResponseError } from './client';
import { studyGuideAPI } from './studyGuide';
import type { StudyGuideGenerationResult } from './types';

const STUDY_GUIDE: StudyGuideGenerationResult = {
  context_truncated: true,
  chunks_used: 40,
  chunks_available: 300,
  study_guide: {
    title: 'Example Guide',
    summary: 'Example summary',
    key_points: ['Point one'],
    important_terms: [{ term: 'Term', definition: 'Definition' }],
    common_mistakes: [{ mistake: 'Mistake', correction: 'Correction' }],
    exam_tips: { lecture_based: ['Tip'], ai_suggestions: ['Suggestion'] },
    difficulty: { level: 'Medium', reason: 'Mixed material' },
    estimated_study_time: '45 minutes',
    prerequisites: ['Algebra'],
    learning_objectives: ['Understand the basics'],
    coverage: { status: 'Partial', estimated_completeness: 40 },
    confidence_notes: '',
  },
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

describe('studyGuideAPI.generate', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token');
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('posts the request body and unwraps the BaseResponse envelope', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: STUDY_GUIDE }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await studyGuideAPI.generate(7, {
      summary_format: 'exam_tips',
      topic_focus: 'Working Memory',
    });

    expect(result).toEqual(STUDY_GUIDE);
    expect(result.study_guide.exam_tips.ai_suggestions).toEqual(['Suggestion']);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/courses/7/study-guide');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(init?.body as string)).toEqual({
      summary_format: 'exam_tips',
      topic_focus: 'Working Memory',
    });
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer test-token');
  });

  it('rejects when the envelope carries no data', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: null }),
      ),
    );

    await expect(
      studyGuideAPI.generate(7, { summary_format: 'overview', topic_focus: 'All Topics' }),
    ).rejects.toBeInstanceOf(MalformedResponseError);
  });
});
