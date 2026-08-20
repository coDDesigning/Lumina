import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MalformedResponseError } from './client';
import { modelsAPI } from './models';
import { userAPI } from './user';
import type { AiModelInfo, User } from './types';

const SAMPLE_MODELS: AiModelInfo[] = [
  {
    id: 'gemini:gemini-3.6-flash',
    provider: 'gemini',
    model: 'gemini-3.6-flash',
    display_name: 'Gemini (gemini-3.6-flash)',
    is_default: true,
  },
  {
    id: 'ollama:llama3.1',
    provider: 'ollama',
    model: 'llama3.1',
    display_name: 'Ollama (llama3.1)',
    is_default: false,
  },
];

const SAMPLE_USER: User = {
  id: 1,
  name: 'Test User',
  email: 'test@example.com',
  role: 'student',
  is_banned: false,
  credits: 49.0,
  preferred_model: 'gemini:gemini-3.6-flash',
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

describe('modelsAPI.list', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token');
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('fetches and unwraps available models catalog', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: SAMPLE_MODELS }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await modelsAPI.list();
    expect(result).toEqual(SAMPLE_MODELS);
    expect(result.length).toBe(2);
    expect(result[0].id).toBe('gemini:gemini-3.6-flash');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/models');
    expect(init?.method).toBe('GET');
  });

  it('rejects when data is null', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: null }),
      ),
    );

    await expect(modelsAPI.list()).rejects.toBeInstanceOf(
      MalformedResponseError,
    );
  });
});

describe('userAPI', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token');
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('updates preferred model and returns updated user', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: SAMPLE_USER }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await userAPI.updatePreferredModel('gemini:gemini-3.6-flash');
    expect(result).toEqual(SAMPLE_USER);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/users/me/model?model_name=');
    expect(init?.method).toBe('PUT');
  });

  it('fetches user credits balance', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: { credits: 49.0 } }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await userAPI.getCredits();
    expect(result).toEqual({ credits: 49.0 });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/users/me/credits');
    expect(init?.method).toBe('GET');
  });
});
