import { apiClient, unwrapData } from './client';
import type {
  BaseResponse,
  CourseSettings,
  CourseSettingsUpdate,
} from './types';

export const settingsAPI = {
  get: async (
    courseId: number,
    options?: RequestInit,
  ): Promise<CourseSettings> => {
    const res = await apiClient.get<BaseResponse<CourseSettings>>(
      `/courses/${courseId}/settings`,
      options,
    );
    return unwrapData(res, 'Get course settings');
  },

  update: async (
    courseId: number,
    updateData: CourseSettingsUpdate,
    options?: RequestInit,
  ): Promise<CourseSettings> => {
    const res = await apiClient.patch<BaseResponse<CourseSettings>>(
      `/courses/${courseId}/settings`,
      updateData,
      options,
    );
    return unwrapData(res, 'Update course settings');
  },
};
