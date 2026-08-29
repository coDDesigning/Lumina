import { apiClient } from './client';
import { AuthResponse, EmailVerificationResult, RegistrationResult, User } from './types';

export const authAPI = {
  login: async (email: string, password: string): Promise<AuthResponse> => {
    // Backend uses OAuth2 password flow which requires form data
    const formData = new URLSearchParams();
    formData.append('username', email); // OAuth2 expects 'username'
    formData.append('password', password);

    return apiClient.postForm<AuthResponse>('/auth/login', formData);
  },

  register: async (
    name: string,
    email: string,
    password: string,
  ): Promise<RegistrationResult> => {
    return apiClient.post<RegistrationResult>('/auth/register', { name, email, password });
  },

  /** Redeem one emailed verification link. Needs no session: the token is the proof. */
  verifyEmail: async (token: string): Promise<EmailVerificationResult> => {
    return apiClient.post<EmailVerificationResult>('/auth/verify-email', { token });
  },

  /**
   * Ask for a fresh link, replacing any outstanding one. The answer is
   * deliberately the same for an unknown address, so it never reveals whether
   * one is registered.
   */
  resendVerification: async (email: string): Promise<EmailVerificationResult> => {
    return apiClient.post<EmailVerificationResult>('/auth/verify-email/resend', { email });
  },

  me: async (options?: RequestInit): Promise<User> => {
    return apiClient.get<User>('/auth/me', options);
  },

  logout: async (): Promise<void> => {
    await apiClient.post('/auth/logout');
  },

  requestPasswordReset: async (email: string): Promise<{ message: string }> => {
    return apiClient.post<{ message: string }>('/auth/reset-password', { email });
  },

  confirmPasswordReset: async (token: string, new_password: string): Promise<{ message: string }> => {
    return apiClient.post<{ message: string }>('/auth/reset-password/confirm', { token, new_password });
  }
};
