import { apiClient, unwrapData } from './client';
import type { BaseResponse, LegalConfigResponse } from './types';

export const legalAPI = {
  getConfig: async (options?: RequestInit): Promise<LegalConfigResponse> => {
    const res = await apiClient.get<BaseResponse<LegalConfigResponse>>('/legal/config', options);
    return unwrapData(res, 'Get legal policy configuration');
  },
};
