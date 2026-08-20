import { apiClient, unwrapData } from './client';
import type {
  BaseResponse,
  GeneratedOutputDetail,
  GeneratedOutputSummary,
} from './types';

export const generatedOutputsAPI = {
  list: async (
    courseId: number,
    options?: RequestInit,
  ): Promise<GeneratedOutputSummary[]> => {
    const res = await apiClient.get<BaseResponse<GeneratedOutputSummary[]>>(
      `/courses/${courseId}/generated-outputs`,
      options,
    );
    return unwrapData(res, 'Generated output history');
  },

  get: async (
    courseId: number,
    outputId: number,
    options?: RequestInit,
  ): Promise<GeneratedOutputDetail> => {
    const res = await apiClient.get<BaseResponse<GeneratedOutputDetail>>(
      `/courses/${courseId}/generated-outputs/${outputId}`,
      options,
    );
    return unwrapData(res, 'Generated output');
  },
};
