import { apiClient, unwrapData } from './client';
import { BaseResponse, ReverseQuizRequest, ReverseQuizResponse } from './types';

export async function generateReverseQuiz(
  courseId: number,
  request: ReverseQuizRequest,
  signal?: AbortSignal
): Promise<ReverseQuizResponse> {
  const response = await apiClient.post<BaseResponse<ReverseQuizResponse>>(
    `/courses/${courseId}/reverse-quiz`,
    request,
    { signal }
  );
  return unwrapData(response, 'Reverse Quiz');
}

export async function getReverseQuizzes(
  courseId: number,
  signal?: AbortSignal
): Promise<ReverseQuizResponse[]> {
  const response = await apiClient.get<BaseResponse<ReverseQuizResponse[]>>(
    `/courses/${courseId}/reverse-quizzes`,
    { signal }
  );
  return unwrapData(response, 'Reverse Quizzes list');
}
