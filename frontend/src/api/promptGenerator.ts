import { apiClient, unwrapData } from './client';
import type {
  BaseResponse,
  PromptGenerationRequest,
  PromptGenerationResponse,
} from './types';

export const promptGeneratorAPI = {
  generate: async (
    request: PromptGenerationRequest,
    options?: RequestInit,
  ): Promise<PromptGenerationResponse> => {
    const res = await apiClient.post<BaseResponse<PromptGenerationResponse>>(
      '/prompt-generator',
      request,
      options,
    );
    return unwrapData(res, 'Prompt generator');
  },
};
