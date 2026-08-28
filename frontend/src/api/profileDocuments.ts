import { apiClient, unwrapData } from './client';
import type {
  BaseResponse,
  ProfileDocumentResponse,
  ProfileDocumentStatusResponse,
  ProfileDocumentUploadResponse,
} from './types';

export const profileDocumentsAPI = {
  list: async (options?: RequestInit): Promise<ProfileDocumentResponse[]> => {
    const res = await apiClient.get<BaseResponse<ProfileDocumentResponse[]>>(
      '/profile-documents',
      options,
    );
    return res.data ?? [];
  },

  getStatus: async (
    documentId: string,
    options?: RequestInit,
  ): Promise<ProfileDocumentStatusResponse> => {
    const res = await apiClient.get<BaseResponse<ProfileDocumentStatusResponse>>(
      `/profile-documents/${documentId}`,
      options,
    );
    return unwrapData(res, 'Profile document status');
  },

  upload: async (
    file: File,
    options?: RequestInit,
  ): Promise<ProfileDocumentUploadResponse> => {
    const formData = new FormData();
    formData.append('document', file);
    return apiClient.postForm<ProfileDocumentUploadResponse>(
      '/profile-documents',
      formData,
      options,
    );
  },

  retry: async (
    documentId: string,
    options?: RequestInit,
  ): Promise<ProfileDocumentStatusResponse> => {
    const res = await apiClient.post<BaseResponse<ProfileDocumentStatusResponse>>(
      `/profile-documents/${documentId}/retry`,
      undefined,
      options,
    );
    return unwrapData(res, 'Profile document retry');
  },

  delete: async (
    documentId: string,
    options?: RequestInit,
  ): Promise<void> => {
    await apiClient.delete<unknown>(
      `/profile-documents/${documentId}`,
      options,
    );
  },
};
