import { apiClient, unwrapData } from './client';
import type {
  BaseResponse,
  ProfileKnowledgeCreate,
  ProfileKnowledgeImport,
  ProfileKnowledgeItem,
  ProfileKnowledgeUpdate,
} from './types';

export const profileKnowledgeAPI = {
  list: async (options?: RequestInit): Promise<ProfileKnowledgeItem[]> => {
    const res = await apiClient.get<BaseResponse<ProfileKnowledgeItem[]>>(
      '/profile-knowledge/',
      options,
    );
    return res.data ?? [];
  },

  get: async (
    id: number,
    options?: RequestInit,
  ): Promise<ProfileKnowledgeItem> => {
    const res = await apiClient.get<BaseResponse<ProfileKnowledgeItem>>(
      `/profile-knowledge/${id}`,
      options,
    );
    return unwrapData(res, 'Profile knowledge');
  },

  create: async (
    data: ProfileKnowledgeCreate,
    options?: RequestInit,
  ): Promise<ProfileKnowledgeItem> => {
    const res = await apiClient.post<BaseResponse<ProfileKnowledgeItem>>(
      '/profile-knowledge/',
      data,
      options,
    );
    return unwrapData(res, 'Profile knowledge creation');
  },

  update: async (
    id: number,
    data: ProfileKnowledgeUpdate,
    options?: RequestInit,
  ): Promise<ProfileKnowledgeItem> => {
    const res = await apiClient.put<BaseResponse<ProfileKnowledgeItem>>(
      `/profile-knowledge/${id}`,
      data,
      options,
    );
    return unwrapData(res, 'Profile knowledge update');
  },

  delete: async (id: number, options?: RequestInit): Promise<void> => {
    await apiClient.delete<BaseResponse<unknown>>(
      `/profile-knowledge/${id}`,
      options,
    );
  },

  importBulk: async (
    payload: ProfileKnowledgeImport,
    options?: RequestInit,
  ): Promise<ProfileKnowledgeItem[]> => {
    const res = await apiClient.post<BaseResponse<ProfileKnowledgeItem[]>>(
      '/profile-knowledge/import',
      payload,
      options,
    );
    return unwrapData(res, 'Profile knowledge bulk import');
  },
};
