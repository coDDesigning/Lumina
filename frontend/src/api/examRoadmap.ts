import { apiClient, unwrapData } from './client';
import type {
  BaseResponse,
  ExamRoadmapRequest,
  ExamRoadmapResult,
} from './types';

export const examRoadmapAPI = {
  generate: async (
    courseId: number,
    request?: ExamRoadmapRequest,
    options?: RequestInit,
  ): Promise<ExamRoadmapResult> => {
    const res = await apiClient.post<BaseResponse<ExamRoadmapResult>>(
      `/courses/${courseId}/exam-roadmap`,
      request ?? {},
      options,
    );
    return unwrapData(res, 'Exam roadmap generation');
  },
};
