import { apiClient, unwrapData } from './client';
import type {
  BaseResponse,
  Course,
  CourseCreate,
  CourseUpdate,
  DocumentResponse,
  DocumentStatusResponse,
  DocumentMaterialKind,
  DocumentUploadResponse,
} from './types';

export const coursesAPI = {
  list: async (options?: RequestInit): Promise<Course[]> => {
    const res = await apiClient.get<BaseResponse<Course[]>>('/courses/', options);
    return res.data ?? [];
  },

  get: async (courseId: number, options?: RequestInit): Promise<Course> => {
    const res = await apiClient.get<BaseResponse<Course>>(
      `/courses/${courseId}`,
      options,
    );
    return unwrapData(res, 'Course');
  },

  create: async (data: CourseCreate): Promise<Course> => {
    const res = await apiClient.post<BaseResponse<Course>>('/courses/', data);
    return unwrapData(res, 'Course creation');
  },

  update: async (courseId: number, data: CourseUpdate): Promise<Course> => {
    const res = await apiClient.put<BaseResponse<Course>>(
      `/courses/${courseId}`,
      data,
    );
    return unwrapData(res, 'Course update');
  },

  delete: async (courseId: number): Promise<void> => {
    await apiClient.delete<unknown>(`/courses/${courseId}`);
  },

  uploadDocument: async (
    courseId: number,
    file: File,
    materialKind: DocumentMaterialKind = 'unspecified',
    options?: RequestInit,
  ): Promise<DocumentUploadResponse> => {
    const formData = new FormData();
    formData.append('document', file);
    formData.append('material_kind', materialKind);
    return apiClient.postForm<DocumentUploadResponse>(
      `/courses/${courseId}/documents`,
      formData,
      options,
    );
  },

  listDocuments: async (
    courseId: number,
    options?: RequestInit,
  ): Promise<DocumentResponse[]> => {
    const res = await apiClient.get<BaseResponse<DocumentResponse[]>>(
      `/courses/${courseId}/documents`,
      options,
    );
    return res.data ?? [];
  },

  getDocumentStatus: (
    courseId: number,
    documentId: string,
    options?: RequestInit,
  ): Promise<DocumentStatusResponse> =>
    apiClient.get<DocumentStatusResponse>(
      `/courses/${courseId}/documents/${documentId}`,
      options,
    ),

  retryDocument: (
    courseId: number,
    documentId: string,
    options?: RequestInit,
  ): Promise<DocumentStatusResponse> =>
    apiClient.post<DocumentStatusResponse>(
      `/courses/${courseId}/documents/${documentId}/retry`,
      undefined,
      options,
    ),

  deleteDocument: async (
    courseId: number,
    documentId: string,
    options?: RequestInit,
  ): Promise<void> => {
    await apiClient.delete<unknown>(
      `/courses/${courseId}/documents/${documentId}`,
      options,
    );
  },
};
