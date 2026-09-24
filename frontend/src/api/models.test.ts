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
    cost_hint: 'Metered (1-2 credits)',
    capabilities: ['study_guide', 'quiz', 'flashcard'],
    description: 'Fast instruction model',
    is_local: false,
    supports_json: true,
  },
  {
    id: 'ollama:llama3.1',
    provider: 'ollama',
    model: 'llama3.1',
    display_name: 'Ollama (llama3.1)',
    is_default: false,
    cost_hint: 'Local execution · Unmetered',
    capabilities: ['study_guide', 'quiz', 'flashcard', 'ai_tutor'],
    description: 'Self-hosted local model',
    is_local: true,
    supports_json: true,
  },
];

const SAMPLE_USER: User = {
  id: 1,
  name: 'Test User',
  email: 'test@example.com',
  role: 'student',
  is_banned: false,
  is_email_verified: true,
  credits: 49.0,
  preferred_model: 'gemini:gemini-3.6-flash',
  education_level: 'unspecified',
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

describe('modelsAPI.test', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token');
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('posts the model id and unwraps the result', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        success: true,
        message: 'Model test completed',
        data: {
          ok: true,
          model_id: 'ollama:llama3.1',
          provider: 'ollama',
          latency_ms: 842,
          error_code: null,
          message: 'The model responded successfully.',
          supports_vision: null,
          base_url: null,
          base_url_fallback: null,
        },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await modelsAPI.test('ollama:llama3.1');
    expect(result.ok).toBe(true);
    expect(result.latency_ms).toBe(842);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/models/test');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toEqual({ model_id: 'ollama:llama3.1' });
  });

  it('sends a null model id as-is, for the caller preferred model', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        success: true,
        message: 'Model test completed',
        data: {
          ok: false,
          model_id: 'gemini:gemini-3.6-flash',
          provider: 'gemini',
          latency_ms: null,
          error_code: 'unavailable',
          message: 'This model is not available.',
          supports_vision: null,
          base_url: null,
          base_url_fallback: null,
        },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await modelsAPI.test(null);
    expect(result.ok).toBe(false);
    expect(result.error_code).toBe('unavailable');

    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(String(init?.body))).toEqual({ model_id: null });
  });

  it('rejects when data is null', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: null }),
      ),
    );

    await expect(modelsAPI.test('ollama:llama3.1')).rejects.toBeInstanceOf(
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
