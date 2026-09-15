import { apiClient, unwrapData } from './client';
import type {
  AdminCreditReason,
  AdminLogEventDetail,
  AdminLogList,
  AdminLogSummary,
  AdminLogTrace,
  AiCostReport,
  BaseResponse,
  Course,
  CreditMutation,
  CreditTransaction,
  User,
} from './types';

export const adminAPI = {
  listLogs: async (params: URLSearchParams, options?: RequestInit): Promise<AdminLogList> => {
    const query = params.toString();
    const res = await apiClient.get<BaseResponse<AdminLogList>>(
      `/admin/logs${query ? `?${query}` : ''}`,
      options,
    );
    return unwrapData(res, 'Admin operational logs');
  },

  summarizeLogs: async (
    params: URLSearchParams,
    options?: RequestInit,
  ): Promise<AdminLogSummary> => {
    const query = params.toString();
    const res = await apiClient.get<BaseResponse<AdminLogSummary>>(
      `/admin/logs/summary${query ? `?${query}` : ''}`,
      options,
    );
    return unwrapData(res, 'Admin operational log summary');
  },

  getLogEvent: async (eventId: string, options?: RequestInit): Promise<AdminLogEventDetail> => {
    const res = await apiClient.get<BaseResponse<AdminLogEventDetail>>(
      `/admin/logs/events/${encodeURIComponent(eventId)}`,
      options,
    );
    return unwrapData(res, 'Admin operational event');
  },

  traceLogEvent: async (eventId: string, options?: RequestInit): Promise<AdminLogTrace> => {
    const res = await apiClient.get<BaseResponse<AdminLogTrace>>(
      `/admin/logs/trace?event_id=${encodeURIComponent(eventId)}`,
      options,
    );
    return unwrapData(res, 'Admin operation trace');
  },

  exportLogs: async (
    params: URLSearchParams,
    format: 'jsonl' | 'csv',
    options?: RequestInit,
  ): Promise<{ blob: Blob; truncated: boolean }> => {
    const query = new URLSearchParams(params);
    query.set('format', format);
    const response = await apiClient.download(`/admin/logs/export?${query}`, options);
    return {
      blob: await response.blob(),
      truncated: response.headers.get('X-Export-Truncated') === 'true',
    };
  },

  getAiCostReport: async (days = 30, options?: RequestInit): Promise<AiCostReport> => {
    const res = await apiClient.get<BaseResponse<AiCostReport>>(
      `/admin/ai-costs?days=${days}`,
      options,
    );
    return unwrapData(res, 'Admin AI cost report');
  },

  listUsers: async (options?: RequestInit): Promise<User[]> => {
    const res = await apiClient.get<BaseResponse<User[]>>(
      '/admin/users',
      options,
    );
    return unwrapData(res, 'Admin list users');
  },

  banUser: async (
    email: string,
    isBanned: boolean,
    options?: RequestInit,
  ): Promise<User> => {
    const encodedEmail = encodeURIComponent(email);
    const res = await apiClient.put<BaseResponse<User>>(
      `/admin/users/${encodedEmail}/ban?is_banned=${isBanned}`,
      undefined,
      options,
    );
    return unwrapData(res, 'Admin ban user');
  },

  changeUserRole: async (
    email: string,
    role: 'admin' | 'user',
    options?: RequestInit,
  ): Promise<User> => {
    const encodedEmail = encodeURIComponent(email);
    const res = await apiClient.put<BaseResponse<User>>(
      `/admin/users/${encodedEmail}/role?role=${role}`,
      undefined,
      options,
    );
    return unwrapData(res, 'Admin change user role');
  },

  changeCredits: async (
    email: string,
    delta: number,
    reason: AdminCreditReason,
    note?: string,
    options?: RequestInit,
  ): Promise<CreditMutation> => {
    const res = await apiClient.post<BaseResponse<CreditMutation>>(
      `/admin/users/${encodeURIComponent(email)}/credits`,
      { delta, reason, note: note || null },
      options,
    );
    return unwrapData(res, 'Admin change credits');
  },

  listUserCreditTransactions: async (
    email: string,
    limit = 20,
    options?: RequestInit,
  ): Promise<CreditTransaction[]> => {
    const res = await apiClient.get<BaseResponse<CreditTransaction[]>>(
      `/admin/users/${encodeURIComponent(email)}/credit-transactions?limit=${limit}`,
      options,
    );
    return unwrapData(res, 'Admin user credit transactions');
  },

  listUserCourses: async (
    email: string,
    options?: RequestInit,
  ): Promise<Course[]> => {
    const res = await apiClient.get<BaseResponse<Course[]>>(
      `/admin/users/${encodeURIComponent(email)}/courses`,
      options,
    );
    return unwrapData(res, 'Admin user courses');
  },
};
