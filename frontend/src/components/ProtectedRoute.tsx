import React, { useEffect, useRef } from 'react';
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom';
import type { User } from '../api/types';
import { useAuth } from '../context/AuthContext';
import { RouteLoading } from '@/app/RouteLoading';
import { useToast } from '@/ui/toastContext';

interface ProtectedRouteProps {
  requiredRole?: User['role'];
}

function RoleDeniedRedirect() {
  const navigate = useNavigate();
  const { showToast } = useToast();
  const hasRedirected = useRef(false);

  useEffect(() => {
    if (hasRedirected.current) {
      return;
    }
    hasRedirected.current = true;
    showToast({
      tone: 'info',
      title: 'Administrator access required',
      message: 'You were redirected to your dashboard.',
    });
    navigate('/dashboard', { replace: true });
  }, [navigate, showToast]);

  return <RouteLoading label="Redirecting to your dashboard" />;
}

export const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ requiredRole }) => {
  const { user, isAuthenticated, isLoading, sessionEndReason } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return <RouteLoading label="Checking your session" />;
  }

  if (!isAuthenticated) {
    return (
      <Navigate
        to="/login"
        state={{ from: location, sessionEndReason: sessionEndReason ?? null }}
        replace
      />
    );
  }

  if (requiredRole && user?.role !== requiredRole) {
    return <RoleDeniedRedirect />;
  }

  return <Outlet />;
};
