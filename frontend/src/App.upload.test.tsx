import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { coursesAPI } from './api/courses';
import { progressAPI } from './api/progress';
import {
  createMockCourse,
  createMockDocument,
  createMockDocumentStatus,
  createMockUploadResponse,
  MockErrors,
} from './test/mocks/api';

vi.mock('./context/CreditContext', () => ({
  useCredits: () => ({
    status: null,
    isLoading: false,
    error: null,
    refresh: vi.fn(),
    isMetered: false,
    costOf: () => null,
    canAfford: () => true,
  }),
}))

vi.mock('./context/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 1,
      name: 'Test Student',
      email: 'student@example.com',
      role: 'student',
      is_banned: false,
      credits: null,
      preferred_model: 'gemini-1.5-flash',
    },
    isAuthenticated: true,
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

vi.mock('./api/courses', () => ({
  coursesAPI: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    listDocuments: vi.fn(),
    getDocumentStatus: vi.fn(),
    uploadDocument: vi.fn(),
    deleteDocument: vi.fn(),
    retryDocument: vi.fn(),
  },
}));

vi.mock('./api/progress', () => ({
  progressAPI: {
    get: vi.fn(),
  },
}));

vi.mock('./api/courseQa', () => ({
  courseQaAPI: {
    ask: vi.fn(),
  },
}));

const mockList = vi.mocked(coursesAPI.list);
const mockListDocuments = vi.mocked(coursesAPI.listDocuments);
const mockGetDocumentStatus = vi.mocked(coursesAPI.getDocumentStatus);
const mockUploadDocument = vi.mocked(coursesAPI.uploadDocument);
const mockGetProgress = vi.mocked(progressAPI.get);

function uploadAlertFor(fileName: string) {
  const alert = screen
    .getAllByRole('alert')
    .find((node) => node.textContent?.includes(fileName));
  if (!alert) {
    throw new Error(`No upload alert found for ${fileName}`);
  }
  return alert;
}

describe('Document Upload UI in Workspace', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockList.mockResolvedValue([createMockCourse({ id: 1, title: 'Operating Systems' })]);
    mockListDocuments.mockResolvedValue([]);
    mockGetDocumentStatus.mockImplementation(async (courseId, docId) =>
      createMockDocumentStatus({
        document: { id: docId, course_id: courseId, original_file_name: 'syllabus.pdf' },
      }),
    );
    mockGetProgress.mockResolvedValue({
      attempts_count: 0,
      average_score: null,
      topic_mastery: [],
    });
  });

  it('uploads a valid file successfully and adds it to the list', async () => {
    const user = userEvent.setup();
    const doc = createMockDocument({
      id: 'doc-1',
      original_file_name: 'syllabus.pdf',
      status: 'uploaded',
    });

    mockUploadDocument.mockResolvedValueOnce(
      createMockUploadResponse({ document: doc, duplicate: false }),
    );

    const { container } = render(
      <MemoryRouter initialEntries={['/courses/1']}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add Sources' })).toBeInTheDocument();
    });

    const file = new File(['dummy content'], 'syllabus.pdf', { type: 'application/pdf' });
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    expect(fileInput).toBeInTheDocument();

    await user.upload(fileInput, file);

    expect(mockUploadDocument).toHaveBeenCalledWith(1, file, 'unspecified');
    await waitFor(() => {
      expect(screen.getByText('syllabus.pdf')).toBeInTheDocument();
    });
  });

  it('displays duplicate notice when uploaded file already exists in the course', async () => {
    const user = userEvent.setup();
    const doc = createMockDocument({
      id: 'doc-1',
      original_file_name: 'lecture1.pdf',
      status: 'uploaded',
    });

    mockUploadDocument.mockResolvedValueOnce(
      createMockUploadResponse({ document: doc, duplicate: true }),
    );

    const { container } = render(
      <MemoryRouter initialEntries={['/courses/1']}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add Sources' })).toBeInTheDocument();
    });

    const file = new File(['dummy content'], 'lecture1.pdf', { type: 'application/pdf' });
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;

    await user.upload(fileInput, file);

    await waitFor(() => {
      expect(
        screen.getByText(/lecture1\.pdf is already in this course/),
      ).toBeInTheDocument();
      expect(screen.getByText(/The original was kept/)).toBeInTheDocument();
    });
  });

  it('handles 409 conflict error when upload/deletion is locked', async () => {
    const user = userEvent.setup();
    mockUploadDocument.mockRejectedValueOnce(
      MockErrors.conflict(
        'The document cannot be deleted while it is being processed.',
      ),
    );

    const { container } = render(
      <MemoryRouter initialEntries={['/courses/1']}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add Sources' })).toBeInTheDocument();
    });

    const file = new File(['content'], 'busy.pdf', { type: 'application/pdf' });
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;

    await user.upload(fileInput, file);

    await waitFor(() => {
      expect(uploadAlertFor('busy.pdf')).toHaveTextContent('The document cannot be deleted while it is being processed.');
    });
  });

  it('handles 413 payload too large error', async () => {
    const user = userEvent.setup();
    mockUploadDocument.mockRejectedValueOnce(
      MockErrors.payloadTooLarge(
        'The file exceeds the maximum allowed upload size of 50 MB.',
      ),
    );

    const { container } = render(
      <MemoryRouter initialEntries={['/courses/1']}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add Sources' })).toBeInTheDocument();
    });

    const file = new File(['giant content'], 'large_video.pdf', { type: 'application/pdf' });
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;

    await user.upload(fileInput, file);

    await waitFor(() => {
      expect(uploadAlertFor('large_video.pdf')).toHaveTextContent('The file exceeds the maximum allowed upload size of 50 MB.');
    });
  });

  it('handles 415 unsupported file type error', async () => {
    const user = userEvent.setup();
    mockUploadDocument.mockRejectedValueOnce(
      MockErrors.unsupportedMediaType(
        'Unsupported file type. Please upload a PDF, TXT, or Markdown file.',
      ),
    );

    const { container } = render(
      <MemoryRouter initialEntries={['/courses/1']}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add Sources' })).toBeInTheDocument();
    });

    const file = new File(['fake pdf content'], 'unsupported.pdf', { type: 'application/pdf' });
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;

    await user.upload(fileInput, file);

    await waitFor(() => {
      expect(uploadAlertFor('unsupported.pdf')).toHaveTextContent('Unsupported file type. Please upload a PDF, TXT, or Markdown file.');
    });
  });

  it('handles 422 validation error', async () => {
    const user = userEvent.setup();
    mockUploadDocument.mockRejectedValueOnce(
      MockErrors.validation([
        { loc: ['body', 'file'], msg: 'Empty file is not permitted' },
      ]),
    );

    const { container } = render(
      <MemoryRouter initialEntries={['/courses/1']}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add Sources' })).toBeInTheDocument();
    });

    const file = new File([], 'empty.txt', { type: 'text/plain' });
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;

    await user.upload(fileInput, file);

    await waitFor(() => {
      expect(uploadAlertFor('empty.txt')).toHaveTextContent('file: Empty file is not permitted');
    });
  });
});
