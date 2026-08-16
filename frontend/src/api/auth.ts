import { apiClient } from './client';
import { AuthResponse, User } from './types';

export const authAPI = {
  login: async (email: string, password: string): Promise<AuthResponse> => {
    // Backend uses OAuth2 password flow which requires form data
    const formData = new URLSearchParams();
    formData.append('username', email); // OAuth2 expects 'username'
    formData.append('password', password);
    
    return apiClient.postForm<AuthResponse>('/auth/login', formData);
  },
  
  register: async (name: string, email: string, password: string): Promise<User> => {
    return apiClient.post<User>('/auth/register', { name, email, password });
  },
  
  me: async (): Promise<User> => {
    return apiClient.get<User>('/auth/me');
  }
};
