import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { userAPI } from '../api/user';
import { describeError } from '../api/errors';
import type { CreditSource, CreditStatus } from '../api/types';
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
  const { isAuthenticated } = useAuth();
  const [status, setStatus] = useState<CreditStatus | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (!isAuthenticated || inFlight.current) {
      return;
    }
    inFlight.current = true;
    setIsLoading(true);
    try {
      setStatus(await userAPI.getCredits());
      setError(null);
    } catch (err) {
      // The previous balance is dropped rather than left to look current, but
      // an unknown balance must never be rendered as an exhausted one.
      setStatus(null);
      setError(describeError(err, 'Your credit balance could not be loaded.').message);
    } finally {
      inFlight.current = false;
      setIsLoading(false);
    }
  }, [isAuthenticated]);

  useEffect(() => {
    if (!isAuthenticated) {
      setStatus(null);
      setError(null);
      return;
    }
    void refresh();
  }, [isAuthenticated, refresh]);

  useEffect(() => {
    if (!isAuthenticated) {
      return;
    }
    // An administrator can grant credits while this tab sits open, so returning
    // to it is the moment the displayed balance is most likely to be wrong.
    const onFocus = () => void refresh();
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        void refresh();
      }
    };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [isAuthenticated, refresh]);

  const isMetered = status != null && status.credits !== null;

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
      value={{ status, isLoading, error, refresh, isMetered, costOf, canAfford }}
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
