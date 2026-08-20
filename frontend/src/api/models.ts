import { apiClient, unwrapData } from './client';
import type { AiModelInfo, BaseResponse } from './types';

export const modelsAPI = {
  list: async (options?: RequestInit): Promise<AiModelInfo[]> => {
    const res = await apiClient.get<BaseResponse<AiModelInfo[]>>(
      '/models',
      options,
    );
    return unwrapData(res, 'AI Models');
  },
};
