import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MalformedResponseError } from './client';
import { flashcardsAPI } from './flashcards';
import type { FlashcardGenerationResult } from './types';

const FLASHCARD_RESULT: FlashcardGenerationResult = {
  context_truncated: false,
  chunks_used: 2,
  chunks_available: 4,
  flashcards: {
    deck_title: 'Operating Systems Flashcards',
    card_count: 2,
    flashcards: [
      {
        card_number: 1,
        difficulty: 'Easy',
        front: 'What is a process?',
        back: 'An executing instance of a program.',
      },
      {
        card_number: 2,
        difficulty: 'Medium',
        front: 'What is a deadlock?',
        back: 'A state where each process waits for a resource held by another.',
      },
    ],
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

describe('flashcardsAPI.generate', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token');
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('posts flashcard request and unwraps deck response', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: FLASHCARD_RESULT }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await flashcardsAPI.generate(10, {
      model: 'gemini:gemini-3.6-flash',
      include_profile_context: true,
    });

    expect(result).toEqual(FLASHCARD_RESULT);
    expect(result.flashcards.deck_title).toBe('Operating Systems Flashcards');
    expect(result.flashcards.flashcards).toHaveLength(2);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/courses/10/flashcards');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(init?.body as string)).toEqual({
      model: 'gemini:gemini-3.6-flash',
      include_profile_context: true,
    });
  });

  it('rejects when envelope carries no data', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: null }),
      ),
    );

    await expect(
      flashcardsAPI.generate(10),
    ).rejects.toBeInstanceOf(MalformedResponseError);
  });
});
