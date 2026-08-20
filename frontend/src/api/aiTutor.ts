import { apiClient, unwrapData } from './client';
import type {
  AiTutorGenerationResult,
  AiTutorRequest,
  BaseResponse,
} from './types';

export const aiTutorAPI = {
  ask: async (
    courseId: number,
    request: AiTutorRequest,
    options?: RequestInit,
  ): Promise<AiTutorGenerationResult> => {
    const res = await apiClient.post<BaseResponse<AiTutorGenerationResult>>(
      `/courses/${courseId}/ai-tutor`,
      request,
      options,
    );
    return unwrapData(res, 'AI tutor');
  },
};
