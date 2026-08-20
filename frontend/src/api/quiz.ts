import { apiClient, unwrapData } from './client';
import type {
  BaseResponse,
  QuizAttemptRequest,
  QuizAttemptResponse,
  QuizGenerationResult,
  QuizRequest,
  QuizSummary,
  QuizView,
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

  list: async (
    courseId: number,
    options?: RequestInit,
  ): Promise<QuizSummary[]> => {
    const res = await apiClient.get<BaseResponse<QuizSummary[]>>(
      `/courses/${courseId}/quizzes`,
      options,
    );
    return unwrapData(res, 'Quiz list');
  },

  get: async (
    courseId: number,
    quizId: number,
    options?: RequestInit,
  ): Promise<QuizView> => {
    const res = await apiClient.get<BaseResponse<QuizView>>(
      `/courses/${courseId}/quizzes/${quizId}`,
      options,
    );
    return unwrapData(res, 'Quiz');
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
