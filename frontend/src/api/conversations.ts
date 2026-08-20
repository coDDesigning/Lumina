import { apiClient, unwrapData } from './client';
import type {
  BaseResponse,
  ConversationDetail,
  ConversationSummary,
} from './types';

export const conversationsAPI = {
  list: async (
    courseId: number,
    options?: RequestInit,
  ): Promise<ConversationSummary[]> => {
    const res = await apiClient.get<BaseResponse<ConversationSummary[]>>(
      `/courses/${courseId}/conversations`,
      options,
    );
    return unwrapData(res, 'Conversation history');
  },

  get: async (
    courseId: number,
    conversationId: number,
    options?: RequestInit,
  ): Promise<ConversationDetail> => {
    const res = await apiClient.get<BaseResponse<ConversationDetail>>(
      `/courses/${courseId}/conversations/${conversationId}`,
      options,
    );
    return unwrapData(res, 'Conversation');
  },
};
