export interface User {
  id: number;
  name: string;
  email: string;
  role: string;
  is_banned: boolean;
  credits: number | null;
  preferred_model: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
}

export interface Course {
  id: number;
  title: string;
  description: string | null;
  owner_id: number;
  created_at: string;
  updated_at: string;
  semester: string | null;
  exam_date: string | null;
  syllabus: string | null;
  topics: string | null;
}

export interface CourseCreate {
  title: string;
  description?: string;
  semester?: string;
  exam_date?: string;
  syllabus?: string;
  topics?: string;
}

export interface CourseUpdate {
  title?: string;
  description?: string;
  semester?: string;
  exam_date?: string;
  syllabus?: string;
  topics?: string;
}

export interface DocumentResponse {
  id: string;
  original_file_name: string;
  file_type: string;
  mime_type: string;
  file_size: number;
  course_id: number;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface DocumentUploadResponse {
  document: DocumentResponse;
  duplicate: boolean;
}

export interface ProcessingJobResponse {
  id: number;
  status: string;
  attempt_count: number;
  max_attempts: number;
  available_at: string;
  started_at: string | null;
  finished_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  processing_stage: string | null;
  failed_stage: string | null;
}

export interface DocumentStatusResponse {
  document: DocumentResponse;
  processing_job: ProcessingJobResponse;
}
