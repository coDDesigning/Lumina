import { apiClient, unwrapData } from './client';
import type {
  BaseResponse,
  CourseQAGenerationResult,
  CourseQARequest,
} from './types';

export const courseQaAPI = {
  ask: async (
    courseId: number,
    request: CourseQARequest,
    options?: RequestInit,
  ): Promise<CourseQAGenerationResult> => {
    const res = await apiClient.post<BaseResponse<CourseQAGenerationResult>>(
      `/courses/${courseId}/qa`,
      request,
      options,
    );
    return unwrapData(res, 'Course Q&A');
  },
};
