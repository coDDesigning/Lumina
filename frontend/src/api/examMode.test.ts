import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MalformedResponseError } from './client';
import { examModeAPI } from './examMode';
import type { ExamTopicGuideDocument } from './types';

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: 'OK',
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as Response;
}

function stubFetch(data: unknown, status = 200) {
  const fetchMock = vi.fn<typeof fetch>(async () =>
    jsonResponse(status === 200 ? { success: true, message: 'ok', data } : data, status),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function calledUrl(fetchMock: ReturnType<typeof stubFetch>): string {
  return fetchMock.mock.calls[0][0] as string;
}

function topicGuideFixture(
  overrides: Partial<ExamTopicGuideDocument> = {},
): ExamTopicGuideDocument {
  return {
    version: 1,
    output_type: 'exam_topic_guide',
    topic_key: 'hashing',
    display_label: 'Hashing',
    plan_output_id: 9,
    rank: 1,
    priority_band: 'high',
    title: 'Hashing study guide',
    overview: 'Hash tables map keys to buckets.',
    sections: [
      {
        heading: 'Collision handling',
        body: 'Collisions occur when keys map to the same bucket.',
        key_points: ['Chaining stores colliding entries together.'],
      },
    ],
    key_terms: [{ term: 'Load factor', definition: 'Entries divided by buckets.', citations: [] }],
    common_pitfalls: [],
    what_to_be_able_to_do: ['Compare collision strategies.'],
    coverage: { status: 'Complete', estimated_completeness: 100 },
    confidence_notes: '',
    ...overrides,
  };
}

describe('examModeAPI', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token');
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  describe('reads', () => {
    it('asks for the source inventory of one course', async () => {
      const fetchMock = stubFetch({ documents: [] });

      await examModeAPI.listSources(10);

      expect(calledUrl(fetchMock)).toBe('/api/courses/10/exam-mode/sources');
    });

    it('asks for the topics this student already unlocked', async () => {
      const fetchMock = stubFetch({ unlocked_topic_keys: ['hashing'] });

      const result = await examModeAPI.listEntitlements(10);

      expect(result.unlocked_topic_keys).toEqual(['hashing']);
      expect(calledUrl(fetchMock)).toBe('/api/courses/10/exam-mode/entitlements');
    });

    it('omits the analysis identifier entirely when asking for the latest one', async () => {
      const fetchMock = stubFetch({ generated_output_id: 5 });

      await examModeAPI.getAnalysis(10);

      expect(calledUrl(fetchMock)).toBe('/api/courses/10/exam-mode/analysis');
    });

    it('names a specific analysis when one is asked for', async () => {
      const fetchMock = stubFetch({ generated_output_id: 5 });

      await examModeAPI.getAnalysis(10, 5);

      expect(calledUrl(fetchMock)).toBe('/api/courses/10/exam-mode/analysis?output_id=5');
    });

    it('pages extracted questions and filters them by topic', async () => {
      const fetchMock = stubFetch({ questions: [], total: 0 });

      await examModeAPI.listQuestions(10, 5, {
        topicKey: 'graph traversal',
        limit: 25,
        offset: 50,
      });

      expect(calledUrl(fetchMock)).toBe(
        '/api/courses/10/exam-mode/analysis/5/questions' +
          '?topic_key=graph+traversal&limit=25&offset=50',
      );
    });

    it('sends no question filter at all when none was given', async () => {
      const fetchMock = stubFetch({ questions: [], total: 0 });

      await examModeAPI.listQuestions(10, 5);

      expect(calledUrl(fetchMock)).toBe('/api/courses/10/exam-mode/analysis/5/questions');
    });

    it('reads one saved plan by identifier', async () => {
      const fetchMock = stubFetch({ generated_output_id: 9 });

      await examModeAPI.getPlan(10, 9);

      expect(calledUrl(fetchMock)).toBe('/api/courses/10/exam-mode/plans/9');
    });

    it('validates a saved topic guide before returning it', async () => {
      const stored = topicGuideFixture();
      stubFetch(stored);

      await expect(examModeAPI.getTopicGuide(10, 'hashing')).resolves.toEqual(stored);
    });

    it.each([
      'sections',
      'key_terms',
      'common_pitfalls',
      'what_to_be_able_to_do',
    ] as const)('rejects a saved topic guide without %s', async (field) => {
      stubFetch({ ...topicGuideFixture(), [field]: undefined });

      await expect(examModeAPI.getTopicGuide(10, 'hashing')).rejects.toMatchObject({
        reason: 'invalid_data',
      });
    });

    it('rejects malformed fields nested inside a saved topic guide', async () => {
      stubFetch({
        ...topicGuideFixture(),
        sections: [{ heading: 'Collision handling', body: null, key_points: [] }],
      });

      await expect(examModeAPI.getTopicGuide(10, 'hashing')).rejects.toBeInstanceOf(
        MalformedResponseError,
      );
    });

    it('lists saved plans', async () => {
      const fetchMock = stubFetch({ plans: [] });

      await examModeAPI.listPlans(10);

      expect(calledUrl(fetchMock)).toBe('/api/courses/10/exam-mode/plans');
    });

    it('reads the saved mock exam and review sheet', async () => {
      const mock = stubFetch({ quiz_id: 3 });
      await examModeAPI.getMockExam(10);
      expect(calledUrl(mock)).toBe('/api/courses/10/exam-mode/mock-exam');

      vi.unstubAllGlobals();
      const sheet = stubFetch({ plan_output_id: 9 });
      await examModeAPI.getReviewSheet(10);
      expect(calledUrl(sheet)).toBe('/api/courses/10/exam-mode/review-sheet');
    });
  });

  describe('topic paths', () => {
    // A topic key is student-derived text on its way into a URL path, so a
    // slash or a space in one must not silently become a different endpoint.
    it.each([
      ['graph-traversal', 'graph-traversal'],
      ['b+ trees', 'b%2B%20trees'],
      ['a/b testing', 'a%2Fb%20testing'],
      ['naïve bayes', 'na%C3%AFve%20bayes'],
    ])('encodes %s into the path', async (topicKey, encoded) => {
      const fetchMock = stubFetch(topicGuideFixture({ topic_key: topicKey }));

      await examModeAPI.getTopicGuide(10, topicKey);

      expect(calledUrl(fetchMock)).toBe(
        `/api/courses/10/exam-mode/topics/${encoded}/guide`,
      );
    });

    it.each([
      ['getTopicSummary', 'summary'],
      ['getSimilarQuestions', 'similar-questions'],
    ] as const)('routes %s to its own suffix', async (method, suffix) => {
      const fetchMock = stubFetch({});

      await examModeAPI[method](10, 'hashing');

      expect(calledUrl(fetchMock)).toBe(
        `/api/courses/10/exam-mode/topics/hashing/${suffix}`,
      );
    });
  });

  describe('writes', () => {
    it('sends only the documents the student selected', async () => {
      const fetchMock = stubFetch({ analysis: { generated_output_id: 5 } });

      await examModeAPI.analyse(10, { document_ids: ['abc', 'def'] });

      expect(fetchMock).toHaveBeenCalledWith(
        '/api/courses/10/exam-mode/analysis',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ document_ids: ['abc', 'def'] }),
        }),
      );
    });

    it('sends a rescan to its own endpoint, not the analysis one', async () => {
      // The two are separately priced, so they must never share a path.
      const fetchMock = stubFetch({ analysis: { generated_output_id: 6 } });

      await examModeAPI.rescan(10, { document_ids: ['abc'] });

      expect(calledUrl(fetchMock)).toBe('/api/courses/10/exam-mode/analysis/rescan');
    });

    it('creates a plan from the selection the student reviewed', async () => {
      const fetchMock = stubFetch({ generated_output_id: 9 });

      await examModeAPI.createPlan(10, {
        analysis_output_id: 5,
        selected_topic_keys: ['hashing', 'sorting'],
        high_priority_topic_keys: ['hashing'],
        selection_mode: 'manual',
      });

      expect(fetchMock).toHaveBeenCalledWith(
        '/api/courses/10/exam-mode/plans',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            analysis_output_id: 5,
            selected_topic_keys: ['hashing', 'sorting'],
            high_priority_topic_keys: ['hashing'],
            selection_mode: 'manual',
          }),
        }),
      );
    });

    it.each([
      ['generateTopicGuide', 'guide'],
      ['generateTopicSummary', 'summary'],
    ] as const)('posts %s against its plan', async (method, suffix) => {
      const fetchMock = stubFetch({ generated_output_id: 11 });

      await examModeAPI[method](10, 'hashing', { plan_output_id: 9 });

      expect(fetchMock).toHaveBeenCalledWith(
        `/api/courses/10/exam-mode/topics/hashing/${suffix}`,
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ plan_output_id: 9 }),
        }),
      );
    });

    it.each([
      ['generateTopicPractice', 'practice'],
      ['generateTopicExam', 'exam'],
    ] as const)('posts %s with the requested count', async (method, suffix) => {
      const fetchMock = stubFetch({ generated_output_id: 12 });

      await examModeAPI[method](10, 'hashing', { plan_output_id: 9, question_count: 5 });

      expect(fetchMock).toHaveBeenCalledWith(
        `/api/courses/10/exam-mode/topics/hashing/${suffix}`,
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ plan_output_id: 9, question_count: 5 }),
        }),
      );
    });

    it('writes similar questions from source question identifiers', async () => {
      // Identifiers, never pasted text: the paper was transcribed once already.
      const fetchMock = stubFetch({ generated_output_id: 13, source_question_ids: [1] });

      await examModeAPI.generateSimilarQuestions(10, 'hashing', {
        plan_output_id: 9,
        source_question_ids: [1, 2],
        question_count: 4,
        difficulty_policy: 'match_source',
      });

      expect(fetchMock).toHaveBeenCalledWith(
        '/api/courses/10/exam-mode/topics/hashing/similar-questions',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            plan_output_id: 9,
            source_question_ids: [1, 2],
            question_count: 4,
            difficulty_policy: 'match_source',
          }),
        }),
      );
    });

    it('sends the whole mock configuration in one request', async () => {
      const fetchMock = stubFetch({ generated_output_id: 14 });

      await examModeAPI.generateMockExam(10, {
        plan_output_id: 9,
        question_count: 6,
        duration_minutes: 45,
        question_mix: [{ question_type: 'multiple_choice', count: 6 }],
        topic_keys: ['hashing'],
      });

      expect(fetchMock).toHaveBeenCalledWith(
        '/api/courses/10/exam-mode/mock-exam',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            plan_output_id: 9,
            question_count: 6,
            duration_minutes: 45,
            question_mix: [{ question_type: 'multiple_choice', count: 6 }],
            topic_keys: ['hashing'],
          }),
        }),
      );
    });

    it('writes a review sheet for one plan', async () => {
      const fetchMock = stubFetch({ generated_output_id: 15 });

      await examModeAPI.generateReviewSheet(10, { plan_output_id: 9 });

      expect(fetchMock).toHaveBeenCalledWith(
        '/api/courses/10/exam-mode/review-sheet',
        expect.objectContaining({ method: 'POST', body: JSON.stringify({ plan_output_id: 9 }) }),
      );
    });
  });

  describe('failures and cancellation', () => {
    it('forwards an abort signal through every read', async () => {
      const fetchMock = stubFetch({ documents: [] });
      const controller = new AbortController();

      await examModeAPI.listSources(10, { signal: controller.signal });

      expect(fetchMock).toHaveBeenCalledWith(
        '/api/courses/10/exam-mode/sources',
        expect.objectContaining({ signal: controller.signal }),
      );
    });

    it('refuses a success envelope that carries no data', async () => {
      stubFetch(null);

      await expect(examModeAPI.listSources(10)).rejects.toBeInstanceOf(
        MalformedResponseError,
      );
    });

    it.each([
      ['no_relevant_material', 409],
      ['exam_analysis_required', 409],
      ['exam_topic_not_discovered', 409],
      ['insufficient_credits', 402],
      ['mock_exam_configuration_invalid', 422],
    ])('keeps the %s code the server refused with', async (code, status) => {
      // Three of these share a status, so the header is the only thing that
      // tells them apart and it has to survive the client.
      const fetchMock = vi.fn<typeof fetch>(async () => ({
        ...jsonResponse({ detail: 'refused' }, status),
        headers: new Headers({ 'X-Error-Code': code }),
      }));
      vi.stubGlobal('fetch', fetchMock);

      await expect(examModeAPI.createPlan(10, {
        selected_topic_keys: [],
        high_priority_topic_keys: [],
      })).rejects.toMatchObject({ status, code });
    });
  });
});
