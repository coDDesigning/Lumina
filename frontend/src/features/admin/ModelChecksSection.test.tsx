import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import type { AiModelInfo, ModelTestResult } from '@/api/types';
import { ModelChecksSection } from './ModelChecksSection';
import { modelsAPI } from '@/api/models';

vi.mock('@/api/models', () => ({
  modelsAPI: {
    list: vi.fn(),
    test: vi.fn(),
  },
}));

const mockList = vi.mocked(modelsAPI.list);
const mockTest = vi.mocked(modelsAPI.test);

const GEMINI: AiModelInfo = {
  id: 'gemini:gemini-3.6-flash',
  provider: 'gemini',
  model: 'gemini-3.6-flash',
  display_name: 'Gemini (gemini-3.6-flash)',
  is_default: true,
};

const OLLAMA: AiModelInfo = {
  id: 'ollama:llama3.1',
  provider: 'ollama',
  model: 'llama3.1',
  display_name: 'Ollama (llama3.1)',
  is_default: false,
};

function result(overrides: Partial<ModelTestResult> = {}): ModelTestResult {
  return {
    ok: true,
    model_id: 'gemini:gemini-3.6-flash',
    provider: 'gemini',
    latency_ms: 800,
    error_code: null,
    message: 'The provider confirmed this model is available to your account.',
    supports_vision: null,
    base_url: null,
    base_url_fallback: null,
    ...overrides,
  };
}

function rowFor(name: string): HTMLElement {
  return screen.getByText(name).closest('li') as HTMLElement;
}

describe('ModelChecksSection', () => {
  beforeEach(() => {
    mockList.mockReset();
    mockTest.mockReset();
  });

  it('lists every configured model as Waiting before any test runs', async () => {
    mockList.mockResolvedValue([GEMINI, OLLAMA]);

    render(<ModelChecksSection />);

    expect(await screen.findByText('Gemini (gemini-3.6-flash)')).toBeInTheDocument();
    expect(screen.getByText('Ollama (llama3.1)')).toBeInTheDocument();
    expect(within(rowFor('Gemini (gemini-3.6-flash)')).getByText('Waiting')).toBeInTheDocument();
    expect(within(rowFor('Ollama (llama3.1)')).getByText('Waiting')).toBeInTheDocument();
  });

  it('tests models one at a time, showing Testing then Passed or Failed per row', async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue([GEMINI, OLLAMA]);
    const resolvers: Record<string, (value: ModelTestResult) => void> = {};
    mockTest.mockImplementation(
      (modelId) =>
        new Promise((resolve) => {
          resolvers[String(modelId)] = resolve;
        }),
    );

    render(<ModelChecksSection />);
    await screen.findByText('Gemini (gemini-3.6-flash)');
    await user.click(screen.getByRole('button', { name: 'Test all models' }));

    expect(
      await within(rowFor('Gemini (gemini-3.6-flash)')).findByText('Testing…'),
    ).toBeInTheDocument();
    expect(within(rowFor('Ollama (llama3.1)')).getByText('Waiting')).toBeInTheDocument();
    expect(mockTest).toHaveBeenCalledTimes(1);
    expect(mockTest).toHaveBeenCalledWith('gemini:gemini-3.6-flash');

    resolvers['gemini:gemini-3.6-flash'](result({ latency_ms: 800 }));

    expect(
      await within(rowFor('Ollama (llama3.1)')).findByText('Testing…'),
    ).toBeInTheDocument();
    expect(within(rowFor('Gemini (gemini-3.6-flash)')).getByText('Passed')).toBeInTheDocument();
    expect(mockTest).toHaveBeenCalledTimes(2);
    expect(mockTest).toHaveBeenCalledWith('ollama:llama3.1');

    resolvers['ollama:llama3.1'](
      result({
        ok: false,
        model_id: 'ollama:llama3.1',
        provider: 'ollama',
        latency_ms: null,
        error_code: 'model_not_found',
        message: 'Run `ollama pull llama3.1` on the Ollama machine.',
      }),
    );

    expect(
      await within(rowFor('Ollama (llama3.1)')).findByText('Failed'),
    ).toBeInTheDocument();
    expect(
      within(rowFor('Ollama (llama3.1)')).getByText(
        'Run `ollama pull llama3.1` on the Ollama machine.',
      ),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole('button', { name: 'Test all models' }),
    ).toBeInTheDocument();
  });

  it('moves on to the next model when a request throws', async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue([GEMINI, OLLAMA]);
    mockTest.mockImplementation((modelId) => {
      if (modelId === 'gemini:gemini-3.6-flash') {
        return Promise.reject(new APIError(503, { detail: 'Service unavailable' }));
      }
      return Promise.resolve(result({ model_id: 'ollama:llama3.1', provider: 'ollama' }));
    });

    render(<ModelChecksSection />);
    await screen.findByText('Gemini (gemini-3.6-flash)');
    await user.click(screen.getByRole('button', { name: 'Test all models' }));

    expect(
      await within(rowFor('Gemini (gemini-3.6-flash)')).findByText('Failed'),
    ).toBeInTheDocument();
    expect(
      within(rowFor('Gemini (gemini-3.6-flash)')).getByText('Service unavailable'),
    ).toBeInTheDocument();
    expect(
      await within(rowFor('Ollama (llama3.1)')).findByText('Passed'),
    ).toBeInTheDocument();
    expect(mockTest).toHaveBeenCalledTimes(2);
  });

  it('shows the Ollama base URL and fallback warning on its row', async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue([OLLAMA]);
    mockTest.mockResolvedValue(
      result({
        model_id: 'ollama:llama3.1',
        provider: 'ollama',
        base_url: 'http://127.0.0.1:11434',
        base_url_fallback: true,
      }),
    );

    render(<ModelChecksSection />);
    await screen.findByText('Ollama (llama3.1)');
    await user.click(screen.getByRole('button', { name: 'Test all models' }));

    expect(
      await within(rowFor('Ollama (llama3.1)')).findByText('http://127.0.0.1:11434'),
    ).toBeInTheDocument();
    expect(
      within(rowFor('Ollama (llama3.1)')).getByText(
        'Using the fallback address because the configured OLLAMA_BASE_URL did not resolve.',
      ),
    ).toBeInTheDocument();
  });

  it("reports a model list that couldn't be loaded, and recovers on retry", async () => {
    mockList.mockRejectedValueOnce(new APIError(503, { detail: 'Models are unavailable.' }));
    mockList.mockResolvedValueOnce([GEMINI]);

    render(<ModelChecksSection />);

    expect(await screen.findByText('Models are unavailable.')).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Try again' }));

    expect(await screen.findByText('Gemini (gemini-3.6-flash)')).toBeInTheDocument();
  });

  it('disables Test all models when no models are configured', async () => {
    mockList.mockResolvedValue([]);

    render(<ModelChecksSection />);

    expect(await screen.findByRole('button', { name: 'Test all models' })).toBeDisabled();
    expect(mockTest).not.toHaveBeenCalled();
  });
});
