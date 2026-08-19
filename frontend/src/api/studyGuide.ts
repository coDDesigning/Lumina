import { apiClient, unwrapData } from './client';
import type {
  BaseResponse,
  StudyGuideGenerationResult,
  StudyGuideRequest,
} from './types';

export const studyGuideAPI = {
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
