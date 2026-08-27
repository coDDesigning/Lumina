import { APIError } from '../../api/client';
import type {
  Course,
  DocumentResponse,
  DocumentStatusResponse,
  DocumentUploadResponse,
  User,
} from '../../api/types';

export function createMockUser(overrides: Partial<User> = {}): User {
  return {
    id: 1,
    name: 'Test Student',
    email: 'student@example.com',
    role: 'student',
    is_banned: false,
    credits: 100,
    preferred_model: 'gemini-1.5-flash',
    education_level: 'unspecified',
    ...overrides,
  };
}

export function createMockCourse(overrides: Partial<Course> = {}): Course {
  return {
    id: 1,
    title: 'Computer Systems',
    subject_area: null,
    education_level: 'unspecified',
    description: 'Introduction to operating systems and architecture',
    owner_id: 1,
    created_at: '2026-08-19T10:00:00Z',
    updated_at: '2026-08-19T10:00:00Z',
    semester: 'Fall 2026',
    exam_date: '2026-12-15',
    syllabus: 'Topics include processes, threads, virtual memory, and caching.',
    topics: ['Architecture', 'Process Scheduling', 'Memory Hierarchy'],
    ...overrides,
  };
}

export function createMockDocument(
  overrides: Partial<DocumentResponse> = {},
): DocumentResponse {
  return {
    id: '11111111-1111-1111-1111-111111111111',
    original_file_name: 'lecture1.pdf',
    file_type: 'pdf',
    mime_type: 'application/pdf',
    material_kind: 'unspecified',
    file_size: 1048576,
    course_id: 1,
    status: 'uploaded',
    created_at: '2026-08-19T10:00:00Z',
    updated_at: '2026-08-19T10:00:00Z',
    ...overrides,
  };
}

export function createMockDocumentStatus(
  overrides: {
    document?: Partial<DocumentResponse>;
    job?: Partial<DocumentStatusResponse['processing_job']>;
  } = {},
): DocumentStatusResponse {
  const doc = createMockDocument(overrides.document);
  return {
    document: doc,
    processing_job: {
      id: 1,
      status: 'running',
      attempt_count: 1,
      max_attempts: 3,
      available_at: '2026-08-19T10:00:00Z',
      started_at: '2026-08-19T10:00:01Z',
      finished_at: null,
      last_error_code: null,
      last_error_message: null,
      processing_stage: 'extracting_text',
      failed_stage: null,
      ...overrides.job,
    },
  };
}

export function createMockUploadResponse(
  overrides: Partial<DocumentUploadResponse> = {},
): DocumentUploadResponse {
  return {
    document: createMockDocument(),
    duplicate: false,
    ...overrides,
  };
}

export const MockErrors = {
  unauthorized: (detail = 'Could not validate credentials') =>
    new APIError(401, { detail }),

  conflict: (
    message = 'The document cannot be deleted while it is being processed.',
    code = 'UPLOAD_DOCUMENT_DELETION_IN_PROGRESS',
  ) =>
    new APIError(409, {
      success: false,
      message,
      data: { code },
    }),

  payloadTooLarge: (
    message = 'The file exceeds the maximum allowed upload size of 50 MB.',
    code = 'UPLOAD_FILE_TOO_LARGE',
  ) =>
    new APIError(413, {
      success: false,
      message,
      data: { code },
    }),

  unsupportedMediaType: (
    message = 'Unsupported file type. Please upload a PDF, TXT, or Markdown file.',
    code = 'UPLOAD_UNSUPPORTED_FILE_TYPE',
  ) =>
    new APIError(415, {
      success: false,
      message,
      data: { code },
    }),

  validation: (
    detail = [{ loc: ['body', 'title'], msg: 'Field required' }],
  ) =>
    new APIError(422, {
      detail,
    }),

  notFound: (detail = 'Course not found') =>
    new APIError(404, { detail }),
};
