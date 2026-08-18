import { apiClient, unwrapData } from './client';
import type { BaseResponse, CourseProgressResponse } from './types';

export const progressAPI = {
  get: async (
    courseId: number,
    options?: RequestInit,
  ): Promise<CourseProgressResponse> => {
    const res = await apiClient.get<BaseResponse<CourseProgressResponse>>(
      `/courses/${courseId}/progress`,
      options,
    );
    return unwrapData(res, 'Course progress');
  },
};
