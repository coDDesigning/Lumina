import { apiClient, unwrapData } from './client';
import type {
  BaseResponse,
  CreditStatus,
  CreditTransaction,
  EducationLevel,
  User,
} from './types';

export const userAPI = {
  updatePreferredModel: async (modelName: string): Promise<User> => {
    const res = await apiClient.put<BaseResponse<User>>(
      `/users/me/model?model_name=${encodeURIComponent(modelName)}`,
    );
    return unwrapData(res, 'User model update');
  },

  updateEducationLevel: async (level: EducationLevel): Promise<User> => {
    const res = await apiClient.put<BaseResponse<User>>(
      `/users/me/education-level?education_level=${encodeURIComponent(level)}`,
    );
    return unwrapData(res, 'User education level update');
  },

  getCredits: async (): Promise<CreditStatus> => {
    const res = await apiClient.get<BaseResponse<CreditStatus>>('/users/me/credits');
    return unwrapData(res, 'User credits');
  },

  getCreditTransactions: async (limit = 20): Promise<CreditTransaction[]> => {
    const res = await apiClient.get<BaseResponse<CreditTransaction[]>>(
      `/users/me/credit-transactions?limit=${limit}`,
    );
    return unwrapData(res, 'User credit transactions');
  },
};
