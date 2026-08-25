import { render, renderHook, screen, waitFor, act } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CreditProvider, useCredits } from './CreditContext';
import CreditBalance from '../components/credits/CreditBalance';
import CreditExhaustedNotice from '../components/credits/CreditExhaustedNotice';
import { userAPI } from '../api/user';
import { APIError } from '../api/client';
import type { CreditStatus } from '../api/types';

const authState = vi.hoisted(() => ({ userId: 1 as number | null }));

vi.mock('../api/user', () => ({
  userAPI: {
    getCredits: vi.fn(),
    getCreditTransactions: vi.fn(),
    updatePreferredModel: vi.fn(),
  },
}));

vi.mock('./AuthContext', () => ({
  useAuth: () => ({
    isAuthenticated: authState.userId !== null,
    user: authState.userId === null ? null : { id: authState.userId },
  }),
}));

const mockGetCredits = vi.mocked(userAPI.getCredits);

function status(overrides: Partial<CreditStatus> = {}): CreditStatus {
  return {
    credits: 12,
    metering_enabled: true,
    monthly_grant: 50,
    balance_cap: 100,
    next_grant_at: '2026-09-01T00:00:00Z',
    generation_costs: {
      study_guide: 1,
      quiz: 1,
      quiz_open_ended: 2,
      flashcard: 1,
      ai_tutor: 1,
      course_qa: 1,
      prompt_generator: 1,
    },
    ...overrides,
  };
}

const wrapper = ({ children }: { children: ReactNode }) => (
  <CreditProvider>{children}</CreditProvider>
);

beforeEach(() => {
  authState.userId = 1;
  mockGetCredits.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('CreditProvider', () => {
  it('reads the balance from the credits endpoint, not the user snapshot', async () => {
    mockGetCredits.mockResolvedValue(status({ credits: 37 }));
    const { result } = renderHook(() => useCredits(), { wrapper });

    await waitFor(() => expect(result.current.status?.credits).toBe(37));
    expect(mockGetCredits).toHaveBeenCalled();
    expect(result.current.isMetered).toBe(true);
  });

  it('treats an unmetered account as having no credit UI at all', async () => {
    mockGetCredits.mockResolvedValue(
      status({ credits: null, monthly_grant: null, generation_costs: {} }),
    );
    const { result } = renderHook(() => useCredits(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isMetered).toBe(false);
    expect(result.current.canAfford('study_guide')).toBe(true);
  });

  it('does not report an unreadable balance as an exhausted one', async () => {
    mockGetCredits.mockRejectedValue(new APIError(500, { detail: 'boom' }));
    const { result } = renderHook(() => useCredits(), { wrapper });

    await waitFor(() => expect(result.current.error).toBeTruthy());
    expect(result.current.status).toBeNull();
    expect(result.current.isMetered).toBe(false);
    expect(result.current.canAfford('quiz')).toBe(true);
  });

  it('compares the balance against the cost, not against zero', async () => {
    mockGetCredits.mockResolvedValue(status({ credits: 1 }));
    const { result } = renderHook(() => useCredits(), { wrapper });

    await waitFor(() => expect(result.current.status?.credits).toBe(1));
    expect(result.current.canAfford('quiz')).toBe(true);
    expect(result.current.canAfford('quiz_open_ended')).toBe(false);
    expect(result.current.costOf('quiz_open_ended')).toBe(2);
  });

  it('picks up credits granted by an administrator when the tab regains focus', async () => {
    mockGetCredits.mockResolvedValueOnce(status({ credits: 0 }));
    const { result } = renderHook(() => useCredits(), { wrapper });
    await waitFor(() => expect(result.current.status?.credits).toBe(0));

    mockGetCredits.mockResolvedValueOnce(status({ credits: 20 }));
    await act(async () => {
      window.dispatchEvent(new Event('focus'));
    });

    await waitFor(() => expect(result.current.status?.credits).toBe(20));
    expect(result.current.canAfford('study_guide')).toBe(true);
  });

  it('queues a trailing refresh when another refresh is already in flight', async () => {
    let resolveFirst: (value: CreditStatus) => void = () => {};
    mockGetCredits
      .mockReturnValueOnce(
        new Promise<CreditStatus>((resolve) => {
          resolveFirst = resolve;
        }),
      )
      .mockResolvedValueOnce(status({ credits: 8 }));
    const { result } = renderHook(() => useCredits(), { wrapper });
    await waitFor(() => expect(mockGetCredits).toHaveBeenCalledTimes(1));

    act(() => {
      void result.current.refresh();
    });
    expect(mockGetCredits).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveFirst(status({ credits: 12 }));
    });

    await waitFor(() => expect(mockGetCredits).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.status?.credits).toBe(8));
  });

  it('does not apply an in-flight balance after the authenticated account changes', async () => {
    let resolveFirst: (value: CreditStatus) => void = () => {};
    mockGetCredits
      .mockReturnValueOnce(
        new Promise<CreditStatus>((resolve) => {
          resolveFirst = resolve;
        }),
      )
      .mockResolvedValueOnce(status({ credits: 7 }));
    const { result, rerender } = renderHook(() => useCredits(), { wrapper });
    await waitFor(() => expect(mockGetCredits).toHaveBeenCalledTimes(1));

    authState.userId = 2;
    rerender();
    await waitFor(() => expect(mockGetCredits).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.status?.credits).toBe(7));

    await act(async () => {
      resolveFirst(status({ credits: 99 }));
    });

    expect(result.current.status?.credits).toBe(7);
  });

  it('clears a settled balance while a different account is loading', async () => {
    let resolveSecond: (value: CreditStatus) => void = () => {};
    mockGetCredits
      .mockResolvedValueOnce(status({ credits: 12 }))
      .mockReturnValueOnce(
        new Promise<CreditStatus>((resolve) => {
          resolveSecond = resolve;
        }),
      );
    const { result, rerender } = renderHook(() => useCredits(), { wrapper });
    await waitFor(() => expect(result.current.status?.credits).toBe(12));

    authState.userId = 2;
    rerender();
    expect(result.current.status).toBeNull();
    await waitFor(() => expect(mockGetCredits).toHaveBeenCalledTimes(2));

    await act(async () => {
      resolveSecond(status({ credits: 7 }));
    });

    await waitFor(() => expect(result.current.status?.credits).toBe(7));
  });
});

describe('CreditBalance', () => {
  it('never renders a loading balance as zero', async () => {
    let resolve: (value: CreditStatus) => void = () => {};
    mockGetCredits.mockReturnValue(
      new Promise<CreditStatus>((r) => {
        resolve = r;
      }),
    );

    render(
      <CreditProvider>
        <CreditBalance source="study_guide" />
      </CreditProvider>,
    );

    expect(screen.queryByText(/^0/)).toBeNull();
    expect(screen.getByText('—')).toBeInTheDocument();

    await act(async () => {
      resolve(status({ credits: 4 }));
    });
    await waitFor(() => expect(screen.getByText('4')).toBeInTheDocument());
  });

  it('says the balance is unavailable rather than empty when the read fails', async () => {
    mockGetCredits.mockRejectedValue(new APIError(503, { detail: 'down' }));

    render(
      <CreditProvider>
        <CreditBalance source="study_guide" />
      </CreditProvider>,
    );

    await waitFor(() =>
      expect(screen.getByText('Balance unavailable')).toBeInTheDocument(),
    );
  });

  it('renders nothing for an account credits do not apply to', async () => {
    mockGetCredits.mockResolvedValue(status({ credits: null }));

    const { container } = render(
      <CreditProvider>
        <CreditBalance source="study_guide" />
      </CreditProvider>,
    );

    await waitFor(() => expect(mockGetCredits).toHaveBeenCalled());
    await waitFor(() => expect(container.textContent).toBe(''));
  });
});

describe('CreditExhaustedNotice', () => {
  it('states both real recovery routes and offers neither a purchase', async () => {
    mockGetCredits.mockResolvedValue(status({ credits: 0 }));

    render(
      <CreditProvider>
        <CreditExhaustedNotice source="study_guide" action="a study guide" />
      </CreditProvider>,
    );

    await waitFor(() =>
      expect(screen.getByText(/credits refresh on/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/contact an administrator/i)).toBeInTheDocument();
    expect(screen.queryByText(/buy|purchase|top up|upgrade/i)).toBeNull();
  });

  it('names the cost of the action that was refused', async () => {
    mockGetCredits.mockResolvedValue(status({ credits: 1 }));

    render(
      <CreditProvider>
        <CreditExhaustedNotice source="quiz_open_ended" action="a quiz" />
      </CreditProvider>,
    );

    await waitFor(() =>
      expect(screen.getByText(/costs 2 credits and you have 1 left/i)).toBeInTheDocument(),
    );
  });

  it('offers a refresh action that re-reads the balance', async () => {
    mockGetCredits.mockResolvedValue(status({ credits: 0 }));

    render(
      <CreditProvider>
        <CreditExhaustedNotice source="study_guide" action="a study guide" />
      </CreditProvider>,
    );

    const button = await screen.findByRole('button', { name: /refresh balance/i });
    mockGetCredits.mockResolvedValue(status({ credits: 20 }));

    await act(async () => {
      button.click();
    });

    await waitFor(() => expect(mockGetCredits).toHaveBeenCalledTimes(2));
  });
});
