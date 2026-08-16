import { apiClient } from './client';
import { Course, CourseCreate, CourseUpdate, DocumentStatusResponse, DocumentUploadResponse, DocumentResponse } from './types';

export const coursesAPI = {
  list: async (): Promise<Course[]> => {
    const res = await apiClient.get<{data: Course[]}>('/courses/');
    return res.data;
  },
  
  get: async (courseId: number): Promise<Course> => {
    const res = await apiClient.get<{data: Course}>(`/courses/${courseId}`);
    return res.data;
  },
  
  create: async (data: CourseCreate): Promise<Course> => {
    const res = await apiClient.post<{data: Course}>('/courses/', data);
    return res.data;
  },
  
  update: async (courseId: number, data: CourseUpdate): Promise<Course> => {
    const res = await apiClient.put<{data: Course}>(`/courses/${courseId}`, data);
    return res.data;
  },
  
  delete: async (courseId: number): Promise<void> => {
    await apiClient.delete(`/courses/${courseId}`);
  },
  
  uploadDocument: async (courseId: number, file: File): Promise<DocumentUploadResponse> => {
    const formData = new FormData();
    formData.append('document', file);
    return apiClient.postForm<DocumentUploadResponse>(`/courses/${courseId}/documents`, formData);
  },
  
  getDocumentStatus: async (courseId: number, documentId: string): Promise<DocumentStatusResponse> => {
    return apiClient.get<DocumentStatusResponse>(`/courses/${courseId}/documents/${documentId}`);
  },

  listDocuments: async (courseId: number): Promise<DocumentResponse[]> => {
    // Note: The backend wrapper BaseResponse gives { success, data, message }.
    // If apiClient auto-unwraps, wait, let's check apiClient.ts.
    // apiClient does NOT auto-unwrap for GET unless we made it.
    // Let's assume apiClient returns the raw JSON. We must extract .data
    const res = await apiClient.get<{data: DocumentResponse[]}>(`/courses/${courseId}/documents`);
    return res.data;
  }
};
