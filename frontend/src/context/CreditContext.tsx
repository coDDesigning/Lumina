import { createContext, useCallback, useContext, type ReactNode } from 'react';
import { queryKeys } from '../api/queryKeys';
import { userAPI } from '../api/user';
import type { CreditSource, CreditStatus } from '../api/types';
import { useQuery } from '../lib/query/useQuery';
import { useAuth } from './AuthContext';

/**
 * The authoritative frontend credit balance.
 *
 * It reads `/users/me/credits` rather than the authenticated user snapshot
 * because only that endpoint materialises an owed monthly grant. A balance
 * taken from `auth/me` would show a stale zero on the first of the month and
 * never recover on its own.
 */
interface CreditContextType {
  status: CreditStatus | null;
  isLoading: boolean;
  /** Set when the balance could not be read. Not the same as a zero balance. */
  error: string | null;
  refresh: () => Promise<void>;
  /** True once a real balance is known and metering applies to this account. */
  isMetered: boolean;
  costOf: (source: CreditSource) => number | null;
  canAfford: (source: CreditSource) => boolean;
}

const CreditContext = createContext<CreditContextType | undefined>(undefined);

export const CreditProvider = ({ children }: { children: ReactNode }) => {
  const { isAuthenticated, user } = useAuth();
  const identity = user?.id ?? null;

  const query = useQuery<CreditStatus>({
    key: isAuthenticated && identity !== null ? queryKeys.credits(identity) : null,
    fetcher: ({ signal }) => userAPI.getCredits({ signal }),
    fallbackMessage: 'Your credit balance could not be loaded.',
    refetchOnFocus: true,
    onRefetchError: 'discard',
  });

  const status = query.data ?? null;
  const error = query.error?.message ?? null;
  const isMetered = status != null && status.credits !== null;

  const { refetch } = query;
  const refresh = useCallback(() => refetch(), [refetch]);

  const costOf = useCallback(
    (source: CreditSource) => status?.generation_costs?.[source] ?? null,
    [status],
  );

  const canAfford = useCallback(
    (source: CreditSource) => {
      if (status == null || status.credits === null) {
        return true;
      }
      return status.credits >= (status.generation_costs?.[source] ?? 1);
    },
    [status],
  );

  return (
    <CreditContext.Provider
      value={{
        status,
        isLoading: query.status === 'pending',
        error,
        refresh,
        isMetered,
        costOf,
        canAfford,
      }}
    >
      {children}
    </CreditContext.Provider>
  );
};

// eslint-disable-next-line react-refresh/only-export-components
export const useCredits = () => {
  const context = useContext(CreditContext);
  if (context === undefined) {
    throw new Error('useCredits must be used within a CreditProvider');
  }
  return context;
};
