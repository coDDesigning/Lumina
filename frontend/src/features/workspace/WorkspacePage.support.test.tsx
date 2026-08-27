import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Workspace } from '@/data/workspaces';
import { ToastProvider } from '@/ui/ToastProvider';
import WorkspacePage from './WorkspacePage';

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 1,
      name: 'Admin User',
      email: 'admin@example.com',
      role: 'admin',
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

vi.mock('@/api/documents', () => ({
  documentsAPI: {
    list: vi.fn().mockResolvedValue([
      {
        id: 'doc-1',
        original_file_name: 'lecture-1.pdf',
        file_type: 'pdf',
        mime_type: 'application/pdf',
        material_kind: 'slides',
        file_size: 1024,
        course_id: 10,
        status: 'ready',
        created_at: '2026-08-20T00:00:00Z',
        updated_at: '2026-08-20T00:00:00Z',
      },
    ]),
  },
}));

vi.mock('@/api/generatedOutputs', () => ({
  generatedOutputsAPI: {
    list: vi.fn().mockResolvedValue([
      {
        id: 101,
        course_id: 10,
        title: 'Midterm Study Guide',
        output_type: 'study_guide',
        content: '# Summary',
        model_used: 'gemini-1.5-flash',
        created_at: '2026-08-21T00:00:00Z',
      },
    ]),
  },
}));

vi.mock('@/api/conversations', () => ({
  conversationsAPI: {
    list: vi.fn().mockResolvedValue([]),
    get: vi.fn().mockResolvedValue({ id: 1, title: 'Chat', conversation_type: 'course_qa', messages: [] }),
  },
}));

const supportWorkspace: Workspace = {
  id: '10',
  ownerId: 2,
  ownerName: 'Alice Johnson',
  ownerEmail: 'alice@example.com',
  name: 'Machine Learning',
  subjectArea: 'Computer Science',
  educationLevel: 'undergraduate',
  semester: 'Fall 2026',
  examDate: '2026-12-15',
  topics: ['Neural Networks', 'SVM'],
  syllabus: 'Intro to ML models',
  progress: null,
  updatedAt: '2026-08-20',
  accent: 'blue',
};

describe('WorkspacePage — Read-Only Support View', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders read-only banner with course owner name and disables mutation actions', async () => {
    render(
      <ToastProvider>
        <MemoryRouter>
          <WorkspacePage workspace={supportWorkspace} />
        </MemoryRouter>
      </ToastProvider>,
    );

    // 1. Persistent Read-Only Support banner with owner's name
    expect(screen.getByText(/Viewing course owned by/i)).toBeInTheDocument();
    expect(screen.getByText(/Alice Johnson/i)).toBeInTheDocument();

    // 2. Read-Only Support badge in header
    expect(screen.getByText('Read-Only Support', { selector: 'span' })).toBeInTheDocument();

    // 3. Course settings and credit balance omitted from header actions
    expect(screen.queryByRole('link', { name: 'Course settings' })).not.toBeInTheDocument();

    // 4. Sources panel omits "Add Sources" and kind selector
    expect(screen.queryByRole('button', { name: 'Add Sources' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Adding as/i)).not.toBeInTheDocument();

    // 5. Conversation panel omits "New conversation" and composer form
    expect(screen.queryByRole('button', { name: 'New conversation' })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Ask anything about/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Send' })).not.toBeInTheDocument();
    expect(
      screen.getByText(/Chat and AI generation are disabled in read-only support view/i),
    ).toBeInTheDocument();

    // 6. Outputs panel omits "Make something" generator buttons
    expect(screen.queryByText('Make something')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Study guide' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Practice quiz' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Flashcards' })).not.toBeInTheDocument();

    // 7. Made for you outputs rail is still visible
    expect(screen.getByText(/Made for you/i)).toBeInTheDocument();
  });
});
