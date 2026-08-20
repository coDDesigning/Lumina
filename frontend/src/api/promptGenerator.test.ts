import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MalformedResponseError } from './client';
import { promptGeneratorAPI } from './promptGenerator';
import type { PromptGenerationResponse } from './types';

const PROMPT_RESULT: PromptGenerationResponse = {
  generated_prompt: 'You are an expert tutor. Explain quantum computing in 3 key principles.',
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

describe('promptGeneratorAPI.generate', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token');
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('posts description and unwraps the generated prompt', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: PROMPT_RESULT }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await promptGeneratorAPI.generate({
      description: 'Quantum computing summary',
      model: 'gemini:gemini-3.6-flash',
    });

    expect(result).toEqual(PROMPT_RESULT);
    expect(result.generated_prompt).toBe(
      'You are an expert tutor. Explain quantum computing in 3 key principles.',
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/prompt-generator');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(init?.body as string)).toEqual({
      description: 'Quantum computing summary',
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
      promptGeneratorAPI.generate({ description: 'Test prompt' }),
    ).rejects.toBeInstanceOf(MalformedResponseError);
  });
});
