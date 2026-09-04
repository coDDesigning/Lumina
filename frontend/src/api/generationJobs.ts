import { apiClient, unwrapData } from './client';
import type { BaseResponse, GenerationJob, GenerationJobAccepted } from './types';

export const generationJobsAPI = {
  list: async (courseId: number, options?: RequestInit): Promise<GenerationJob[]> => {
    const response = await apiClient.get<BaseResponse<GenerationJob[]>>(
      `/courses/${courseId}/generation-jobs`,
      options,
    );
    return unwrapData(response, 'Generation jobs');
  },

  get: async (
    courseId: number,
    jobId: number,
    options?: RequestInit,
  ): Promise<GenerationJob> => {
    const response = await apiClient.get<BaseResponse<GenerationJob>>(
      `/courses/${courseId}/generation-jobs/${jobId}`,
      options,
    );
    return unwrapData(response, 'Generation job');
  },

  retry: async (
    courseId: number,
    jobId: number,
    options?: RequestInit,
  ): Promise<GenerationJobAccepted> => {
    const response = await apiClient.post<BaseResponse<GenerationJobAccepted>>(
      `/courses/${courseId}/generation-jobs/${jobId}/retry`,
      undefined,
      options,
    );
    return unwrapData(response, 'Generation retry');
  },

  dismiss: async (
    courseId: number,
    jobId: number,
    options?: RequestInit,
  ): Promise<GenerationJob> => {
    const response = await apiClient.post<BaseResponse<GenerationJob>>(
      `/courses/${courseId}/generation-jobs/${jobId}/dismiss`,
      undefined,
      options,
    );
    return unwrapData(response, 'Generation dismissal');
  },
};
