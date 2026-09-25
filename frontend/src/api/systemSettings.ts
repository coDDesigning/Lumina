import { APIError, apiClient, unwrapData } from './client';
import { describeError } from './errors';
import type { BaseResponse } from './types';

export type SettingKind =
  | 'boolean'
  | 'integer'
  | 'float'
  | 'enum'
  | 'text'
  | 'json'
  | 'list';

export type SettingScope = 'overridable' | 'container_managed' | 'compose_managed';

export type SettingRisk = 'low' | 'medium' | 'high';

export type SettingSource = 'override' | 'environment' | 'default' | 'missing';

export type RestartState =
  | 'queued'
  | 'draining'
  | 'restarting'
  | 'ready'
  | 'failed'
  | 'rolled_back';

export interface SystemSettingRow {
  key: string;
  section: string;
  label: string;
  help: string;
  kind: SettingKind;
  scope: SettingScope;
  risk: SettingRisk;
  secret: boolean;
  choices: string[];
  minimum: number | null;
  maximum: number | null;
  requires_confirmation: boolean;
  editable: boolean;
  source: SettingSource;
  has_override: boolean;
  configured: boolean;
  value: string | null;
  default: string | null;
}

export interface RestartStatus {
  request_id: string;
  state: RestartState;
  target_revision: number;
  requested_at: string;
  updated_at: string;
  drain_deadline: string | null;
  detail: string | null;
  actor_id: number | null;
  changed_keys: string[];
}

export interface SystemSettingsInventory {
  sections: string[];
  settings: SystemSettingRow[];
  active_revision: number;
  saved_revision: number;
  pending_restart: boolean;
  pending_keys: string[];
  override_count: number;
  saved_at: string | null;
  supervised_restart: boolean;
  self_hosted: boolean;
  restart: RestartStatus | null;
  rolled_back_from: number | null;
}

export interface RestartAccepted {
  request_id: string;
  target_revision: number;
  state: RestartState;
  in_flight: {
    documents?: number;
    profile_documents?: number;
    generations?: number;
    total?: number;
  };
}

export interface SystemSettingsUpdate {
  expected_revision: number;
  values?: Record<string, string | null>;
  reset?: string[];
}

export interface SettingFieldError {
  key: string | null;
  message: string;
}

export const systemSettingsAPI = {
  get: async (options?: RequestInit): Promise<SystemSettingsInventory> => {
    const res = await apiClient.get<BaseResponse<SystemSettingsInventory>>(
      '/admin/system-settings',
      options,
    );
    return unwrapData(res, 'System settings');
  },

  update: async (
    payload: SystemSettingsUpdate,
    options?: RequestInit,
  ): Promise<SystemSettingsInventory> => {
    const res = await apiClient.patch<BaseResponse<SystemSettingsInventory>>(
      '/admin/system-settings',
      payload,
      options,
    );
    return unwrapData(res, 'System settings update');
  },

  resetKey: async (
    key: string,
    expectedRevision: number,
    options?: RequestInit,
  ): Promise<SystemSettingsInventory> => {
    const res = await apiClient.delete<BaseResponse<SystemSettingsInventory>>(
      `/admin/system-settings/${encodeURIComponent(key)}?expected_revision=${expectedRevision}`,
      options,
    );
    return unwrapData(res, 'System settings reset');
  },

  resetAll: async (
    expectedRevision: number,
    options?: RequestInit,
  ): Promise<SystemSettingsInventory> => {
    const res = await apiClient.delete<BaseResponse<SystemSettingsInventory>>(
      '/admin/system-settings',
      {
        ...options,
        body: JSON.stringify({ expected_revision: expectedRevision, confirm: true }),
      },
    );
    return unwrapData(res, 'System settings reset all');
  },

  restart: async (
    expectedRevision: number,
    options?: RequestInit,
  ): Promise<RestartAccepted> => {
    const res = await apiClient.post<BaseResponse<RestartAccepted>>(
      '/admin/system-settings/restarts',
      { expected_revision: expectedRevision },
      options,
    );
    return unwrapData(res, 'Restart request');
  },

  restartStatus: async (
    requestId: string,
    options?: RequestInit,
  ): Promise<SystemSettingsInventory> => {
    const res = await apiClient.get<BaseResponse<SystemSettingsInventory>>(
      `/admin/system-settings/restarts/${encodeURIComponent(requestId)}`,
      options,
    );
    return unwrapData(res, 'Restart status');
  },
};

export interface DescribedSettingsError {
  message: string;
  code: string | null;
  fieldErrors: SettingFieldError[];
}

export function describeSettingsError(
  error: unknown,
  fallback: string,
): DescribedSettingsError {
  const described = describeError(error, fallback);
  const fieldErrors: SettingFieldError[] = [];

  if (error instanceof APIError && typeof error.data === 'object' && error.data !== null) {
    const detail = (error.data as { detail?: unknown }).detail;
    if (Array.isArray(detail)) {
      for (const item of detail) {
        if (typeof item !== 'object' || item === null) continue;
        const entry = item as { key?: unknown; message?: unknown };
        if (typeof entry.message !== 'string') continue;
        fieldErrors.push({
          key: typeof entry.key === 'string' ? entry.key : null,
          message: entry.message,
        });
      }
    }
  }

  const message =
    fieldErrors.length > 0 && described.message === 'An API error occurred'
      ? fieldErrors[0].message
      : described.message;

  return { message, code: described.code, fieldErrors };
}
