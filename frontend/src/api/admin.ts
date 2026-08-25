import { apiClient, unwrapData } from './client';
import type {
  AdminCreditReason,
  AiCostReport,
  BaseResponse,
  CreditMutation,
  CreditTransaction,
  User,
} from './types';

export const adminAPI = {
  getAiCostReport: async (days = 30): Promise<AiCostReport> => {
    const res = await apiClient.get<BaseResponse<AiCostReport>>(
      `/admin/ai-costs?days=${days}`,
    );
    return unwrapData(res, 'Admin AI cost report');
  },

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

  changeCredits: async (
    email: string,
    delta: number,
    reason: AdminCreditReason,
    note?: string,
  ): Promise<CreditMutation> => {
    const res = await apiClient.post<BaseResponse<CreditMutation>>(
      `/admin/users/${encodeURIComponent(email)}/credits`,
      { delta, reason, note: note || null },
    );
    return unwrapData(res, 'Admin change credits');
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
