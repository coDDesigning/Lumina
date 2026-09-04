import React, { createContext, useContext, useEffect, useRef, useState, ReactNode } from 'react';
import { APIError, SessionEndReason } from '../api/client';
import { User } from '../api/types';
import { authAPI } from '../api/auth';
import { queryCache } from '../lib/query/cache';

export type { SessionEndReason };

export interface AuthContextType {
  user: User | null;
  isLoading: boolean;
  login: (token: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
  isAuthenticated: boolean;
  sessionEndReason: SessionEndReason | null;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [sessionEndReason, setSessionEndReason] = useState<SessionEndReason | null>(null);
  const requestGeneration = useRef(0);

  const fetchUser = async () => {
    const request = requestGeneration.current + 1;
    requestGeneration.current = request;
    const token = localStorage.getItem('token');
    if (!token) {
      if (requestGeneration.current === request) {
        setUser(null);
        setIsLoading(false);
      }
      return;
    }

    try {
      const userData = await authAPI.me();
      if (requestGeneration.current !== request || localStorage.getItem('token') !== token) {
        return;
      }
      setUser(userData);
      setSessionEndReason(null);
    } catch (caught) {
      if (requestGeneration.current !== request || localStorage.getItem('token') !== token) {
        return;
      }
      console.error('Failed to fetch user', caught);
      localStorage.removeItem('token');
      queryCache.clear();
      setUser(null);
      if (caught instanceof APIError && caught.code === 'account_banned') {
        setSessionEndReason('banned');
      } else {
        setSessionEndReason('unauthorized');
      }
    } finally {
      if (requestGeneration.current === request) {
        setIsLoading(false);
      }
    }
  };

  useEffect(() => {
    fetchUser();
    
    // Listen for unauthorized events from API client
    const handleUnauthorized = (event: Event) => {
      const reason: SessionEndReason =
        event instanceof CustomEvent && event.detail?.reason === 'banned'
          ? 'banned'
          : 'unauthorized';
      requestGeneration.current += 1;
      queryCache.clear();
      setUser(null);
      setIsLoading(false);
      setSessionEndReason(reason);
    };
    
    window.addEventListener('auth:unauthorized', handleUnauthorized);
    return () => window.removeEventListener('auth:unauthorized', handleUnauthorized);
  }, []);

  const login = async (token: string) => {
    queryCache.clear();
    setSessionEndReason(null);
    localStorage.setItem('token', token);
    setIsLoading(true);
    await fetchUser();
  };

  const logout = async () => {
    requestGeneration.current += 1;
    try {
      if (localStorage.getItem('token')) {
        await authAPI.logout();
      }
    } catch {
      // Ignore network errors during logout
    }
    localStorage.removeItem('token');
    queryCache.clear();
    setUser(null);
    setSessionEndReason(null);
    setIsLoading(false);
  };

  return (
    <AuthContext.Provider value={{
      user,
      isLoading,
      login,
      logout,
      refreshUser: fetchUser,
      isAuthenticated: !!user,
      sessionEndReason,
    }}>
      {children}
    </AuthContext.Provider>
  );
};

// eslint-disable-next-line react-refresh/only-export-components
export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
