import { apiClient, unwrapData } from './client';
import type {
  BaseResponse,
  GenerationJobAccepted,
  QuizAttemptRequest,
  QuizAttemptResponse,
  QuizGenerationResult,
  QuizHistoryItem,
  QuizAnswerSubmission,
  QuizRequest,
  QuizSessionStartResult,
  QuizSessionView,
  QuizSummary,
  QuizView,
} from './types';

export const quizAPI = {
  enqueue: async (
    courseId: number,
    request: QuizRequest,
    options?: RequestInit,
  ): Promise<GenerationJobAccepted> => {
    const res = await apiClient.post<BaseResponse<GenerationJobAccepted>>(
      `/courses/${courseId}/quiz/jobs`,
      request,
      options,
    );
    return unwrapData(res, 'Quiz generation job');
  },

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

  listAttempts: async (
    courseId: number,
    quizId: number,
    options?: RequestInit,
  ): Promise<QuizHistoryItem[]> => {
    const res = await apiClient.get<BaseResponse<QuizHistoryItem[]>>(
      `/courses/${courseId}/quizzes/${quizId}/attempts`,
      options,
    );
    return unwrapData(res, 'Quiz attempts');
  },

  getAttempt: async (
    courseId: number,
    quizId: number,
    attemptId: number,
    options?: RequestInit,
  ): Promise<QuizAttemptResponse> => {
    const res = await apiClient.get<BaseResponse<QuizAttemptResponse>>(
      `/courses/${courseId}/quizzes/${quizId}/attempts/${attemptId}`,
      options,
    );
    return unwrapData(res, 'Quiz attempt');
  },

  /**
   * Open a sitting of a timed quiz, or rejoin the one already open.
   *
   * The server owns `started_at` and `expires_at`; neither is ever sent. A
   * timed quiz refuses `submitAttempt`, because that endpoint would take the
   * client's word for how long the sitting took.
   */
  startSession: async (
    courseId: number,
    quizId: number,
    options?: RequestInit,
  ): Promise<QuizSessionStartResult> => {
    const res = await apiClient.post<BaseResponse<QuizSessionStartResult>>(
      `/courses/${courseId}/quizzes/${quizId}/sessions`,
      undefined,
      options,
    );
    return unwrapData(res, 'Timed session');
  },

  getSession: async (
    courseId: number,
    quizId: number,
    sessionId: number,
    options?: RequestInit,
  ): Promise<QuizSessionView> => {
    const res = await apiClient.get<BaseResponse<QuizSessionView>>(
      `/courses/${courseId}/quizzes/${quizId}/sessions/${sessionId}`,
      options,
    );
    return unwrapData(res, 'Timed session');
  },

  /**
   * Save one draft answer. The write answers with the whole saved set, so a
   * client never has to guess what stuck.
   */
  saveSessionAnswer: async (
    courseId: number,
    quizId: number,
    sessionId: number,
    questionId: number,
    answer: QuizAnswerSubmission,
    options?: RequestInit,
  ): Promise<QuizSessionView> => {
    const res = await apiClient.put<BaseResponse<QuizSessionView>>(
      `/courses/${courseId}/quizzes/${quizId}/sessions/${sessionId}/answers/${questionId}`,
      answer,
      options,
    );
    return unwrapData(res, 'Timed session answer');
  },

  /**
   * Finalise a sitting. Exactly-once on the server, so a retry after a dropped
   * connection returns the attempt the first call already produced.
   */
  submitSession: async (
    courseId: number,
    quizId: number,
    sessionId: number,
    options?: RequestInit,
  ): Promise<QuizAttemptResponse> => {
    const res = await apiClient.post<BaseResponse<QuizAttemptResponse>>(
      `/courses/${courseId}/quizzes/${quizId}/sessions/${sessionId}/submit`,
      undefined,
      options,
    );
    return unwrapData(res, 'Timed session submission');
  },
};
