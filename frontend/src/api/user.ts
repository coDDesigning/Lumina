import { apiClient, unwrapData } from './client';
import type {
  BaseResponse,
  CreditStatus,
  CreditTransaction,
  EducationLevel,
  User,
  UserApiKeys,
  UserApiKeysUpdateRequest,
} from './types';

export const userAPI = {
  updatePreferredModel: async (modelName: string, options?: RequestInit): Promise<User> => {
    const res = await apiClient.put<BaseResponse<User>>(
      `/users/me/model?model_name=${encodeURIComponent(modelName)}`,
      undefined,
      options,
    );
    return unwrapData(res, 'User model update');
  },

  updateEducationLevel: async (
    level: EducationLevel,
    options?: RequestInit,
  ): Promise<User> => {
    const res = await apiClient.put<BaseResponse<User>>(
      `/users/me/education-level?education_level=${encodeURIComponent(level)}`,
      undefined,
      options,
    );
    return unwrapData(res, 'User education level update');
  },

  getCredits: async (options?: RequestInit): Promise<CreditStatus> => {
    const res = await apiClient.get<BaseResponse<CreditStatus>>('/users/me/credits', options);
    return unwrapData(res, 'User credits');
  },

  getCreditTransactions: async (
    limit = 20,
    options?: RequestInit,
  ): Promise<CreditTransaction[]> => {
    const res = await apiClient.get<BaseResponse<CreditTransaction[]>>(
      `/users/me/credit-transactions?limit=${limit}`,
      options,
    );
    return unwrapData(res, 'User credit transactions');
  },

  changePassword: async (current_password: string, new_password: string, options?: RequestInit): Promise<void> => {
    const res = await apiClient.put<BaseResponse<null>>(
      '/users/me/password',
      { current_password, new_password },
      options,
    );
    unwrapData(res, 'Change password');
  },

  getApiKeys: async (options?: RequestInit): Promise<UserApiKeys> => {
    const res = await apiClient.get<BaseResponse<UserApiKeys>>('/users/me/api-keys', options);
    return unwrapData(res, 'User API keys');
  },

  updateApiKeys: async (
    keys: UserApiKeysUpdateRequest,
    options?: RequestInit,
  ): Promise<UserApiKeys> => {
    const res = await apiClient.put<BaseResponse<UserApiKeys>>(
      '/users/me/api-keys',
      keys,
      options,
    );
    return unwrapData(res, 'Update API keys');
  },
};
