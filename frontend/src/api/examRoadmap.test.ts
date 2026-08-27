import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MalformedResponseError } from './client';
import { examRoadmapAPI } from './examRoadmap';
import type { ExamRoadmapResult } from './types';

const ROADMAP_RESULT: ExamRoadmapResult = {
  generated_output_id: 42,
  roadmap: {
    version: 1,
    output_type: 'exam_roadmap',
    course_id: 7,
    exam_date: '2026-09-15',
    generated_on: '2026-08-27',
    starts_on: '2026-08-27',
    days_until_exam: 19,
    scheduled_days: 20,
    lead_in_days: 0,
    horizon: 'standard',
    materials_available: true,
    attempts_considered: 2,
    roadmap_version: 1,
    adapted_from_output_id: null,
    ranked_topics: [
      {
        topic: 'Sorting',
        source: 'syllabus',
        syllabus_position: 0,
        importance: 1.0,
        mastery_percentage: 50,
        questions_answered: 4,
        priority: 0.75,
      },
    ],
    days: [
      {
        day_index: 1,
        date: '2026-08-27',
        kind: 'study',
        is_exam_day: false,
        focus: 'First pass: Sorting',
        topics: [
          {
            topic: 'Sorting',
            goal: 'Close the gaps in Sorting',
            pass_number: 1,
            source: 'syllabus',
            syllabus_position: 0,
            importance: 1.0,
            mastery_percentage: 50,
            questions_answered: 4,
            priority: 0.75,
            material_status: 'resolved',
            materials: [
              {
                document_id: '11111111-1111-1111-1111-111111111111',
                document_label: 'Lecture 1',
                page_start: 1,
                page_end: 5,
              },
            ],
            citations: [],
          },
        ],
      },
    ],
    deferred_topics: [],
    notes: [],
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

describe('examRoadmapAPI.generate', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token');
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('posts request options and unwraps the BaseResponse envelope', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: ROADMAP_RESULT }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await examRoadmapAPI.generate(7, {
      max_topics_per_day: 2,
      include_materials: true,
    });

    expect(result).toEqual(ROADMAP_RESULT);
    expect(result.generated_output_id).toBe(42);
    expect(result.roadmap.horizon).toBe('standard');
    expect(result.roadmap.days).toHaveLength(1);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/courses/7/exam-roadmap');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(init?.body as string)).toEqual({
      max_topics_per_day: 2,
      include_materials: true,
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

    await expect(examRoadmapAPI.generate(7)).rejects.toBeInstanceOf(
      MalformedResponseError,
    );
  });
});
