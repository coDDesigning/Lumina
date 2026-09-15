import { apiClient, unwrapData } from './client';
import type { BaseResponse, ClientErrorAccepted, ClientErrorReport } from './types';

export const clientErrorsAPI = {
  report: async (
    payload: ClientErrorReport,
    options?: RequestInit,
  ): Promise<ClientErrorAccepted> => {
    const response = await apiClient.postDiagnostic<BaseResponse<ClientErrorAccepted>>(
      '/client-errors',
      payload,
      options,
    );
    return unwrapData(response, 'Client error report');
  },
};
