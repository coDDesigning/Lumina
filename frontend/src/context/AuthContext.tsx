import React, { createContext, useContext, useEffect, useRef, useState, ReactNode } from 'react';
import { User } from '../api/types';
import { authAPI } from '../api/auth';
import { queryCache } from '../lib/query/cache';

interface AuthContextType {
  user: User | null;
  isLoading: boolean;
  login: (token: string) => Promise<void>;
  logout: () => void;
  refreshUser: () => Promise<void>;
  isAuthenticated: boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
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
    } catch (caught) {
      if (requestGeneration.current !== request || localStorage.getItem('token') !== token) {
        return;
      }
      console.error('Failed to fetch user', caught);
      localStorage.removeItem('token');
      queryCache.clear();
      setUser(null);
    } finally {
      if (requestGeneration.current === request) {
        setIsLoading(false);
      }
    }
  };

  useEffect(() => {
    fetchUser();
    
    // Listen for unauthorized events from API client
    const handleUnauthorized = () => {
      requestGeneration.current += 1;
      queryCache.clear();
      setUser(null);
      setIsLoading(false);
    };
    
    window.addEventListener('auth:unauthorized', handleUnauthorized);
    return () => window.removeEventListener('auth:unauthorized', handleUnauthorized);
  }, []);

  const login = async (token: string) => {
    queryCache.clear();
    localStorage.setItem('token', token);
    setIsLoading(true);
    await fetchUser();
  };

  const logout = () => {
    requestGeneration.current += 1;
    localStorage.removeItem('token');
    queryCache.clear();
    setUser(null);
    setIsLoading(false);
  };

  return (
    <AuthContext.Provider value={{
      user,
      isLoading,
      login,
      logout,
      refreshUser: fetchUser,
      isAuthenticated: !!user
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
