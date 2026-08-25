import { apiClient, unwrapData } from './client';
import type {
  BaseResponse,
  CreditStatus,
  CreditTransaction,
  EducationLevel,
  User,
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
};
