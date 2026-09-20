import { apiClient, unwrapData } from './client';
import type { AiModelInfo, BaseResponse, ModelTestResult } from './types';

export const modelsAPI = {
  list: async (options?: RequestInit): Promise<AiModelInfo[]> => {
    const res = await apiClient.get<BaseResponse<AiModelInfo[]>>(
      '/models',
      options,
    );
    return unwrapData(res, 'AI Models');
  },

  test: async (modelId: string | null, options?: RequestInit): Promise<ModelTestResult> => {
    const res = await apiClient.post<BaseResponse<ModelTestResult>>(
      '/models/test',
      { model_id: modelId },
      options,
    );
    return unwrapData(res, 'Model test');
  },
};
