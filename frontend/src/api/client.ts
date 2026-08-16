// Simple fetch wrapper that automatically adds Auth token
const BASE_URL = '/api';

export class APIError extends Error {
  constructor(public status: number, public data: unknown) {
    let message = 'An API error occurred';
    if (typeof data === 'object' && data !== null && 'detail' in data) {
      const detail = (data as Record<string, unknown>).detail;
      if (typeof detail === 'string') {
        message = detail;
      } else if (Array.isArray(detail) && detail.length > 0) {
        const first = detail[0];
        if (typeof first === 'object' && first !== null && 'msg' in first) {
          const loc = Array.isArray(first.loc) ? first.loc.slice(1).join('.') : '';
          message = loc ? `${loc}: ${String(first.msg)}` : String(first.msg);
        }
      }
    }
    super(message);
    this.name = 'APIError';
  }
}

async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem('token');
  const headers = new Headers(options.headers);
  
  if (!headers.has('Content-Type') && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }

  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const url = `${BASE_URL}${endpoint}`;
  
  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (!response.ok) {
    let errorData: unknown = null;
    try {
      errorData = await response.json();
    } catch {
      errorData = { detail: response.statusText };
    }
    
    if (response.status === 401) {
      // Auto logout on 401
      localStorage.removeItem('token');
      window.dispatchEvent(new Event('auth:unauthorized'));
    }
    
    throw new APIError(response.status, errorData);
  }

  // Handle empty responses
  const text = await response.text();
  if (!text) {
    return {} as T;
  }
  
  return JSON.parse(text) as T;
}

export const apiClient = {
  get: <T>(endpoint: string, options?: RequestInit) => 
    request<T>(endpoint, { ...options, method: 'GET' }),
    
  post: <T>(endpoint: string, data?: unknown, options?: RequestInit) => 
    request<T>(endpoint, {
      ...options,
      method: 'POST',
      body: data ? JSON.stringify(data) : undefined,
    }),
    
  put: <T>(endpoint: string, data?: unknown, options?: RequestInit) => 
    request<T>(endpoint, {
      ...options,
      method: 'PUT',
      body: data ? JSON.stringify(data) : undefined,
    }),
    
  delete: <T>(endpoint: string, options?: RequestInit) => 
    request<T>(endpoint, { ...options, method: 'DELETE' }),
    
  // For FormData (e.g. document uploads or OAuth forms)
  postForm: <T>(endpoint: string, formData: FormData | URLSearchParams, options?: RequestInit) => {
    const headers = new Headers(options?.headers);
    
    if (formData instanceof URLSearchParams) {
      headers.set('Content-Type', 'application/x-www-form-urlencoded');
    }
    
    return request<T>(endpoint, {
      ...options,
      method: 'POST',
      body: formData,
      headers,
    });
  }
};
