import type { ReactNode } from 'react';
import { Navigate } from 'react-router-dom';
import { RouteLoading } from '@/app/RouteLoading';
import { useLegalPolicies } from './useLegalPolicies';

export function LegalGate({ children }: { children: ReactNode }) {
  const { enabled, isSettled } = useLegalPolicies();

  if (!isSettled) {
    return <RouteLoading label="Loading policy" />;
  }

  if (!enabled) {
    return <Navigate to="/" replace />;
  }

  return <>{children}</>;
}
