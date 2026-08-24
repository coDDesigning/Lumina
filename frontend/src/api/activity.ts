import { apiClient, unwrapData } from './client';
import type { ActivityItem, BaseResponse } from './types';

export const activityAPI = {
  list: async (limit?: number, options?: RequestInit): Promise<ActivityItem[]> => {
    const query = limit === undefined ? '' : `?limit=${limit}`;
    const res = await apiClient.get<BaseResponse<ActivityItem[]>>(
      `/activity${query}`,
      options,
    );
    return unwrapData(res, 'Recent activity');
  },
};
