import { apiClient, unwrapData } from './client';
import type {
  BaseResponse,
  GenerationJobAccepted,
  StudyGuideGenerationResult,
  StudyGuideRequest,
} from './types';

export const studyGuideAPI = {
  enqueue: async (
    courseId: number,
    request: StudyGuideRequest,
    options?: RequestInit,
  ): Promise<GenerationJobAccepted> => {
    const res = await apiClient.post<BaseResponse<GenerationJobAccepted>>(
      `/courses/${courseId}/study-guide/jobs`,
      request,
      options,
    );
    return unwrapData(res, 'Study guide generation job');
  },

  generate: async (
    courseId: number,
    request: StudyGuideRequest,
    options?: RequestInit,
  ): Promise<StudyGuideGenerationResult> => {
    const res = await apiClient.post<BaseResponse<StudyGuideGenerationResult>>(
      `/courses/${courseId}/study-guide`,
      request,
      options,
    );
    return unwrapData(res, 'Study guide generation');
  },
};
