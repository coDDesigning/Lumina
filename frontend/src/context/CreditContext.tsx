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
  const { isAuthenticated, user } = useAuth();
  const identity = user?.id ?? null;
  const [snapshot, setSnapshot] = useState<{
    identity: number;
    status: CreditStatus | null;
    error: string | null;
  } | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const inFlight = useRef<{ identity: number; request: Promise<void> } | null>(null);
  const trailingRefresh = useRef(false);
  const identityRef = useRef(identity);
  identityRef.current = identity;

  const refresh = useCallback((): Promise<void> => {
    if (!isAuthenticated || identity === null) {
      return Promise.resolve();
    }
    if (inFlight.current?.identity === identity) {
      trailingRefresh.current = true;
      return inFlight.current.request;
    }

    const requestIdentity = identity;
    const run = async () => {
      setIsLoading(true);
      do {
        trailingRefresh.current = false;
        try {
          const nextStatus = await userAPI.getCredits();
          if (identityRef.current !== requestIdentity) return;
          setSnapshot({ identity: requestIdentity, status: nextStatus, error: null });
        } catch (err) {
          if (identityRef.current !== requestIdentity) return;
          // The previous balance is dropped rather than left to look current, but
          // an unknown balance must never be rendered as an exhausted one.
          setSnapshot({
            identity: requestIdentity,
            status: null,
            error: describeError(err, 'Your credit balance could not be loaded.').message,
          });
        }
      } while (trailingRefresh.current && identityRef.current === requestIdentity);
      if (identityRef.current === requestIdentity) {
        setIsLoading(false);
      }
    };

    const request = run();
    inFlight.current = { identity: requestIdentity, request };
    void request.finally(() => {
      if (inFlight.current?.request === request) {
        inFlight.current = null;
      }
    });
    return request;
  }, [identity, isAuthenticated]);

  useEffect(() => {
    trailingRefresh.current = false;
    setSnapshot(null);
    if (!isAuthenticated) {
      setIsLoading(false);
      return;
    }
    if (inFlight.current?.identity !== identity) {
      void refresh();
    }
  }, [identity, isAuthenticated, refresh]);

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

  const currentSnapshot = snapshot?.identity === identity ? snapshot : null;
  const status = currentSnapshot?.status ?? null;
  const error = currentSnapshot?.error ?? null;
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
