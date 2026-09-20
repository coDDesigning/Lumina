import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import type { ModelTestResult } from '@/api/types';
import { ModelTestControl } from './ModelTestControl';
import { modelsAPI } from '@/api/models';

vi.mock('@/api/models', () => ({
  modelsAPI: {
    test: vi.fn(),
  },
}));

const mockTest = vi.mocked(modelsAPI.test);

const OK_RESULT: ModelTestResult = {
  ok: true,
  model_id: 'ollama:llama3.1',
  provider: 'ollama',
  latency_ms: 1234,
  error_code: null,
  message: 'The model responded successfully.',
  supports_vision: null,
  base_url: null,
  base_url_fallback: null,
};

describe('ModelTestControl', () => {
  beforeEach(() => {
    mockTest.mockReset();
  });

  it('shows Testing while pending and the answer time once the model responds', async () => {
    const user = userEvent.setup();
    let resolveTest: (value: ModelTestResult) => void = () => {};
    mockTest.mockReturnValue(
      new Promise((resolve) => {
        resolveTest = resolve;
      }),
    );

    render(<ModelTestControl modelId="ollama:llama3.1" isAdmin={false} />);
    await user.click(screen.getByRole('button', { name: 'Test model' }));

    expect(await screen.findByRole('button', { name: 'Testing…' })).toBeInTheDocument();

    resolveTest(OK_RESULT);

    expect(await screen.findByText('Answered in 1.2 s')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Test model' })).toBeInTheDocument();
    expect(mockTest).toHaveBeenCalledWith('ollama:llama3.1');
  });

  it("notes that the model can't read images when supports_vision is false", async () => {
    const user = userEvent.setup();
    mockTest.mockResolvedValue({ ...OK_RESULT, supports_vision: false });

    render(<ModelTestControl modelId="ollama:llava" isAdmin={false} />);
    await user.click(screen.getByRole('button', { name: 'Test model' }));

    expect(
      await screen.findByText("This model can't read images, so figures won't be described"),
    ).toBeInTheDocument();
  });

  it('says nothing about vision when the check never learned whether the model supports it', async () => {
    const user = userEvent.setup();
    mockTest.mockResolvedValue({ ...OK_RESULT, supports_vision: null });

    render(<ModelTestControl modelId="ollama:llama3.1" isAdmin={false} />);
    await user.click(screen.getByRole('button', { name: 'Test model' }));

    await screen.findByText('Answered in 1.2 s');
    expect(screen.queryByText(/can't read images/)).not.toBeInTheDocument();
  });

  it('shows the backend message through ErrorState with a working retry when the check fails', async () => {
    const user = userEvent.setup();
    mockTest
      .mockResolvedValueOnce({
        ...OK_RESULT,
        ok: false,
        latency_ms: null,
        error_code: 'model_not_found',
        message: 'Run `ollama pull llama3.1` on the Ollama machine.',
      })
      .mockResolvedValueOnce({ ...OK_RESULT, latency_ms: 300 });

    render(<ModelTestControl modelId="ollama:llama3.1" isAdmin={false} />);
    await user.click(screen.getByRole('button', { name: 'Test model' }));

    expect(
      await screen.findByText('Run `ollama pull llama3.1` on the Ollama machine.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('alert')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Try again' }));

    expect(await screen.findByText('Answered in 0.3 s')).toBeInTheDocument();
    expect(mockTest).toHaveBeenCalledTimes(2);
  });

  it('describes a rate-limited HTTP failure through describeGenerationError, with a retry', async () => {
    const user = userEvent.setup();
    mockTest
      .mockRejectedValueOnce(
        new APIError(
          429,
          { success: false, message: 'Too many requests' },
          'generation_rate_limited',
          '30',
        ),
      )
      .mockResolvedValueOnce({ ...OK_RESULT, latency_ms: 500 });

    render(<ModelTestControl modelId="ollama:llama3.1" isAdmin={false} />);
    await user.click(screen.getByRole('button', { name: 'Test model' }));

    expect(
      await screen.findByText(
        'Too many generation requests were made. Try again in 30 seconds. No credit was charged.',
      ),
    ).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Try again' }));

    expect(await screen.findByText('Answered in 0.5 s')).toBeInTheDocument();
  });

  it('clears the previous result once the selected model changes', async () => {
    const user = userEvent.setup();
    mockTest.mockResolvedValue({ ...OK_RESULT, latency_ms: 900 });

    const { rerender } = render(<ModelTestControl modelId="ollama:llama3.1" isAdmin={false} />);
    await user.click(screen.getByRole('button', { name: 'Test model' }));
    expect(await screen.findByText('Answered in 0.9 s')).toBeInTheDocument();

    rerender(<ModelTestControl modelId="ollama:mistral" isAdmin={false} />);

    await waitFor(() =>
      expect(screen.queryByText('Answered in 0.9 s')).not.toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: 'Test model' })).toBeInTheDocument();
  });

  it('ignores a stale in-flight result and resets to idle when the model changes mid-test', async () => {
    const user = userEvent.setup();
    let resolveFirst: (value: ModelTestResult) => void = () => {};
    mockTest.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
    );

    const { rerender } = render(<ModelTestControl modelId="ollama:llama3.1" isAdmin={false} />);
    await user.click(screen.getByRole('button', { name: 'Test model' }));
    await screen.findByRole('button', { name: 'Testing…' });

    rerender(<ModelTestControl modelId="ollama:mistral" isAdmin={false} />);

    expect(await screen.findByRole('button', { name: 'Test model' })).toBeInTheDocument();

    await act(async () => {
      resolveFirst({ ...OK_RESULT, model_id: 'ollama:llama3.1', latency_ms: 999 });
    });

    expect(screen.queryByText('Answered in 1.0 s')).not.toBeInTheDocument();
    expect(mockTest).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Test model' })).toBeInTheDocument();
  });

  it('shows the Ollama address and fallback warning to an admin', async () => {
    const user = userEvent.setup();
    mockTest.mockResolvedValue({
      ...OK_RESULT,
      base_url: 'http://127.0.0.1:11434',
      base_url_fallback: true,
    });

    render(<ModelTestControl modelId="ollama:llama3.1" isAdmin={true} />);
    await user.click(screen.getByRole('button', { name: 'Test model' }));

    expect(await screen.findByText('http://127.0.0.1:11434')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Using the fallback address because the configured OLLAMA_BASE_URL did not resolve.',
      ),
    ).toBeInTheDocument();
  });

  it('does not warn about the fallback address when the configured one resolved', async () => {
    const user = userEvent.setup();
    mockTest.mockResolvedValue({
      ...OK_RESULT,
      base_url: 'http://127.0.0.1:11434',
      base_url_fallback: false,
    });

    render(<ModelTestControl modelId="ollama:llama3.1" isAdmin={true} />);
    await user.click(screen.getByRole('button', { name: 'Test model' }));

    expect(await screen.findByText('http://127.0.0.1:11434')).toBeInTheDocument();
    expect(
      screen.queryByText(
        'Using the fallback address because the configured OLLAMA_BASE_URL did not resolve.',
      ),
    ).not.toBeInTheDocument();
  });

  it('hides the Ollama address from a non-admin', async () => {
    const user = userEvent.setup();
    mockTest.mockResolvedValue({
      ...OK_RESULT,
      base_url: 'http://127.0.0.1:11434',
      base_url_fallback: true,
    });

    render(<ModelTestControl modelId="ollama:llama3.1" isAdmin={false} />);
    await user.click(screen.getByRole('button', { name: 'Test model' }));

    await screen.findByText('Answered in 1.2 s');
    expect(screen.queryByText('http://127.0.0.1:11434')).not.toBeInTheDocument();
  });
});
