import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MalformedResponseError } from './client';
import { settingsAPI } from './settings';
import type { CourseSettings } from './types';

const MOCK_SETTINGS: CourseSettings = {
  study_mode: 'Exam',
  difficulty: 'Hard',
  question_count: 15,
  summary_length: 'Long',
  detail_level: 'Detailed',
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

describe('settingsAPI', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token');
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('gets course settings successfully', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: MOCK_SETTINGS }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await settingsAPI.get(5);
    expect(result).toEqual(MOCK_SETTINGS);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/courses/5/settings',
      expect.objectContaining({
        headers: expect.any(Headers),
      }),
    );
  });

  it('updates course settings successfully', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        success: true,
        message: 'ok',
        data: { ...MOCK_SETTINGS, difficulty: 'Easy' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await settingsAPI.update(5, { difficulty: 'Easy' });
    expect(result.difficulty).toBe('Easy');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/courses/5/settings',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ difficulty: 'Easy' }),
      }),
    );
  });

  it('rejects when response is malformed', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: null }),
      ),
    );

    await expect(settingsAPI.get(5)).rejects.toBeInstanceOf(
      MalformedResponseError,
    );
  });
});
