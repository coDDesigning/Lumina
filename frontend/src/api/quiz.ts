import { apiClient, unwrapData } from './client';
import type {
  BaseResponse,
  QuizAttemptRequest,
  QuizAttemptResponse,
  QuizGenerationResult,
  QuizRequest,
} from './types';

export const quizAPI = {
  generate: async (
    courseId: number,
    request: QuizRequest,
    options?: RequestInit,
  ): Promise<QuizGenerationResult> => {
    const res = await apiClient.post<BaseResponse<QuizGenerationResult>>(
      `/courses/${courseId}/quiz`,
      request,
      options,
    );
    return unwrapData(res, 'Quiz generation');
  },

  submitAttempt: async (
    courseId: number,
    quizId: number,
    request: QuizAttemptRequest,
    options?: RequestInit,
  ): Promise<QuizAttemptResponse> => {
    const res = await apiClient.post<BaseResponse<QuizAttemptResponse>>(
      `/courses/${courseId}/quizzes/${quizId}/attempts`,
      request,
      options,
    );
    return unwrapData(res, 'Quiz attempt');
  },
};
