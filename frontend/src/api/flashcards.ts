import { apiClient, unwrapData } from './client';
import type {
  BaseResponse,
  FlashcardGenerationResult,
  FlashcardRequest,
  GenerationJobAccepted,
} from './types';

export const flashcardsAPI = {
  generate: async (
    courseId: number,
    request?: FlashcardRequest,
    options?: RequestInit,
  ): Promise<FlashcardGenerationResult> => {
    const res = await apiClient.post<BaseResponse<FlashcardGenerationResult>>(
      `/courses/${courseId}/flashcards`,
      request ?? {},
      options,
    );
    return unwrapData(res, 'Flashcard generation');
  },

  enqueue: async (
    courseId: number,
    request?: FlashcardRequest,
    options?: RequestInit,
  ): Promise<GenerationJobAccepted> => {
    const res = await apiClient.post<BaseResponse<GenerationJobAccepted>>(
      `/courses/${courseId}/flashcards/jobs`,
      request ?? {},
      options,
    );
    return unwrapData(res, 'Flashcard generation enqueue');
  },
};
