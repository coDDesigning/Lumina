import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { generationJobsAPI } from '@/api/generationJobs';
import type { GenerationJobAccepted } from '@/api/types';
import { useGenerationJobs } from './useGenerationJobs';

const mocks = vi.hoisted(() => ({
  refreshCredits: vi.fn().mockResolvedValue(undefined),
  refetch: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('@/api/generationJobs', () => ({
  generationJobsAPI: {
    list: vi.fn(),
    retry: vi.fn(),
    dismiss: vi.fn(),
  },
}));

vi.mock('@/context/CreditContext', () => ({
  useCredits: () => ({ refresh: mocks.refreshCredits }),
}));

vi.mock('@/lib/query/useQuery', () => ({
  useQuery: () => ({
    data: [],
    status: 'success',
    error: null,
    refetchError: null,
    refetch: mocks.refetch,
  }),
}));

describe('useGenerationJobs', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('sends only one paid retry while the first request is pending', async () => {
    let finishRetry!: (result: GenerationJobAccepted) => void;
    vi.mocked(generationJobsAPI.retry).mockReturnValue(
      new Promise<GenerationJobAccepted>((resolve) => {
        finishRetry = resolve;
      }),
    );
    const { result } = renderHook(() => useGenerationJobs(17));

    act(() => {
      void result.current.retry(41);
      void result.current.retry(41);
    });

    expect(generationJobsAPI.retry).toHaveBeenCalledOnce();
    expect(generationJobsAPI.retry).toHaveBeenCalledWith(17, 41);
    expect(result.current.retryingId).toBe(41);

    await act(async () => {
      finishRetry({ job_id: 42, status: 'queued' });
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.retryingId).toBeNull();
  });
});
