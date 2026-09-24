import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Workspace } from '@/data/workspaces';
import { ToastProvider } from '@/ui/ToastProvider';
import { coursesAPI } from '@/api/courses';
import WorkspacePage from './WorkspacePage';

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 1,
      name: 'Student',
      email: 'student@example.com',
      role: 'user',
      is_banned: false,
      is_email_verified: true,
      credits: null,
      preferred_model: 'gemini-1.5-flash',
    },
    isAuthenticated: true,
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

vi.mock('@/context/CreditContext', () => ({
  useCredits: () => ({
    status: null,
    isLoading: false,
    error: null,
    refresh: vi.fn(),
    isMetered: false,
    costOf: () => null,
    canAfford: () => true,
  }),
}));

vi.mock('@/api/courses', () => ({
  coursesAPI: {
    listDocuments: vi.fn(),
    getDocumentStatus: vi.fn(),
    retryDocument: vi.fn(),
    retryDocumentVisuals: vi.fn(),
    deleteDocument: vi.fn(),
    uploadDocument: vi.fn(),
  },
}));

vi.mock('@/api/generatedOutputs', () => ({
  generatedOutputsAPI: {
    list: vi.fn().mockResolvedValue([]),
  },
}));

vi.mock('@/api/conversations', () => ({
  conversationsAPI: {
    list: vi.fn().mockResolvedValue([]),
    get: vi.fn(),
  },
}));

const listDocuments = vi.mocked(coursesAPI.listDocuments);

const workspace: Workspace = {
  id: '10',
  name: 'Machine Learning',
  subjectArea: 'Computer Science',
  educationLevel: 'undergraduate',
  semester: 'Fall 2026',
  examDate: '2026-12-15',
  topics: [],
  syllabus: '',
  progress: null,
  updatedAt: '2026-08-20',
  accent: 'blue',
};

function doc(id: string, name: string, createdAt: string) {
  return {
    id,
    original_file_name: name,
    file_type: 'pdf',
    mime_type: 'application/pdf',
    material_kind: 'unspecified',
    file_size: 1024,
    course_id: 10,
    status: 'ready',
    created_at: createdAt,
    updated_at: createdAt,
  };
}

function renderWorkspace() {
  return render(
    <ToastProvider>
      <MemoryRouter>
        <WorkspacePage workspace={workspace} />
      </MemoryRouter>
    </ToastProvider>,
  );
}

function articleNames(): string[] {
  return screen.getAllByRole('article').map((article) => article.textContent ?? '');
}

describe('WorkspacePage — source ordering', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    listDocuments.mockResolvedValue([
      doc('b-id', 'Bravo.pdf', '2026-08-19T10:00:00Z'),
      doc('a-id', 'Alpha.pdf', '2026-08-17T10:00:00Z'),
      doc('c-id', 'Charlie.pdf', '2026-08-21T10:00:00Z'),
    ]);
  });

  it('shows sources by name by default and switches to newest-first via the Sort control, persisting the choice', async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await waitFor(() => expect(screen.getByText('Sources · 3')).toBeInTheDocument());
    await waitFor(() => {
      const names = articleNames();
      expect(names[0]).toContain('Alpha.pdf');
      expect(names[1]).toContain('Bravo.pdf');
      expect(names[2]).toContain('Charlie.pdf');
    });

    const sortSelect = screen.getByLabelText('Sort');
    await user.selectOptions(sortSelect, 'newest');

    await waitFor(() => {
      const names = articleNames();
      expect(names[0]).toContain('Charlie.pdf');
      expect(names[1]).toContain('Bravo.pdf');
      expect(names[2]).toContain('Alpha.pdf');
    });

    expect(localStorage.getItem('lumina:sources:order')).toBe('newest');
  });

  it('reads a previously stored newest-first preference on mount', async () => {
    localStorage.setItem('lumina:sources:order', 'newest');
    renderWorkspace();

    await waitFor(() => expect(screen.getByText('Sources · 3')).toBeInTheDocument());
    await waitFor(() => {
      const names = articleNames();
      expect(names[0]).toContain('Charlie.pdf');
      expect(names[1]).toContain('Bravo.pdf');
      expect(names[2]).toContain('Alpha.pdf');
    });
  });
});
