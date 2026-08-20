import { apiClient, unwrapData } from './client';
import type { BaseResponse, User } from './types';

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
};
