import { apiClient, unwrapData } from './client';
import type {
  AdConfigResponse,
  AdTelemetryRequest,
  AdTelemetryResponse,
  BaseResponse,
} from './types';

export const adsAPI = {
  getConfig: async (options?: RequestInit): Promise<AdConfigResponse> => {
    const res = await apiClient.get<BaseResponse<AdConfigResponse>>(
      '/ads/config',
      options,
    );
    return unwrapData(res, 'Get ad configuration');
  },

  recordTelemetry: async (
    payload: AdTelemetryRequest,
    options?: RequestInit,
  ): Promise<AdTelemetryResponse> => {
    const res = await apiClient.post<BaseResponse<AdTelemetryResponse>>(
      '/ads/telemetry/impression',
      payload,
      options,
    );
    return unwrapData(res, 'Record ad telemetry');
  },
};
