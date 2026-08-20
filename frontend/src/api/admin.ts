import { apiClient, unwrapData } from './client';
import type {
  BaseResponse,
  CreditMutation,
  CreditTransaction,
  User,
} from './types';

export const adminAPI = {
  listUsers: async (options?: RequestInit): Promise<User[]> => {
    const res = await apiClient.get<BaseResponse<User[]>>(
      '/admin/users',
      options,
    );
    return unwrapData(res, 'Admin list users');
  },

  banUser: async (
    email: string,
    isBanned: boolean,
    options?: RequestInit,
  ): Promise<User> => {
    const encodedEmail = encodeURIComponent(email);
    const res = await apiClient.put<BaseResponse<User>>(
      `/admin/users/${encodedEmail}/ban?is_banned=${isBanned}`,
      undefined,
      options,
    );
    return unwrapData(res, 'Admin ban user');
  },

  changeUserRole: async (
    email: string,
    role: 'admin' | 'user',
    options?: RequestInit,
  ): Promise<User> => {
    const encodedEmail = encodeURIComponent(email);
    const res = await apiClient.put<BaseResponse<User>>(
      `/admin/users/${encodedEmail}/role?role=${role}`,
      undefined,
      options,
    );
    return unwrapData(res, 'Admin change user role');
  },

  grantCredits: async (
    email: string,
    amount: number,
    note?: string,
  ): Promise<CreditMutation> => {
    const res = await apiClient.post<BaseResponse<CreditMutation>>(
      `/admin/users/${encodeURIComponent(email)}/credits/grant`,
      { amount, note: note || null },
    );
    return unwrapData(res, 'Admin grant credits');
  },

  adjustCredits: async (
    email: string,
    delta: number,
    note?: string,
  ): Promise<CreditMutation> => {
    const res = await apiClient.post<BaseResponse<CreditMutation>>(
      `/admin/users/${encodeURIComponent(email)}/credits/adjust`,
      { delta, note: note || null },
    );
    return unwrapData(res, 'Admin adjust credits');
  },

  listUserCreditTransactions: async (
    email: string,
    limit = 20,
  ): Promise<CreditTransaction[]> => {
    const res = await apiClient.get<BaseResponse<CreditTransaction[]>>(
      `/admin/users/${encodeURIComponent(email)}/credit-transactions?limit=${limit}`,
    );
    return unwrapData(res, 'Admin user credit transactions');
  },
};
