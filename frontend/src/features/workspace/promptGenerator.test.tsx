import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '@/App';
import { APIError } from '@/api/client';
import { coursesAPI } from '@/api/courses';
import { progressAPI } from '@/api/progress';
import { promptGeneratorAPI } from '@/api/promptGenerator';
import { createMockCourse } from '@/test/mocks/api';

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

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 1,
      name: 'Deniz Kaya',
      email: 'deniz@uni.edu',
      role: 'user',
      is_banned: false,
      credits: null,
      preferred_model: 'ollama:llama3.1',
      education_level: 'unspecified',
    },
    isAuthenticated: true,
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
  }),
}));

vi.mock('@/api/courses', () => ({
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

vi.mock('@/api/progress', () => ({
  progressAPI: { get: vi.fn(), listAll: vi.fn() },
}));

vi.mock('@/api/promptGenerator', () => ({
  promptGeneratorAPI: { generate: vi.fn() },
}));

const mockList = vi.mocked(coursesAPI.list);
const mockListDocuments = vi.mocked(coursesAPI.listDocuments);
const mockProgress = vi.mocked(progressAPI.get);
const mockGenerate = vi.mocked(promptGeneratorAPI.generate);

function renderWorkspace() {
  return render(
    <MemoryRouter initialEntries={['/courses/1']}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockList.mockResolvedValue([createMockCourse({ id: 1, title: 'Operating Systems' })]);
  mockListDocuments.mockResolvedValue([]);
  mockProgress.mockResolvedValue({
    status: 'no_documents',
    attempts_count: 0,
    average_score: null,
    topic_mastery: [],
  });
  vi.mocked(progressAPI.listAll).mockResolvedValue([]);
});

describe('prompt generator', () => {
  it('is reachable from the composer', async () => {
    renderWorkspace();
    expect(
      await screen.findByRole('button', { name: 'Help me word this' }),
    ).toBeInTheDocument();
  });

  it('writes the generated prompt into the composer', async () => {
    const user = userEvent.setup();
    mockGenerate.mockResolvedValue({
      generated_prompt: 'Summarise the scheduling chapter into exam-ready bullet points.',
    });

    renderWorkspace();
    await user.click(await screen.findByRole('button', { name: 'Help me word this' }));

    await user.type(
      screen.getByLabelText('Prompt description'),
      'exam revision for scheduling',
    );
    await user.click(screen.getByRole('button', { name: 'Write the prompt' }));

    await waitFor(() => {
      expect(mockGenerate).toHaveBeenCalledWith({
        description: 'exam revision for scheduling',
      });
    });

    expect(screen.getByRole('textbox', { name: 'Enter prompt' })).toHaveValue(
      'Summarise the scheduling chapter into exam-ready bullet points.',
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('keeps the dialog open and explains a failure', async () => {
    const user = userEvent.setup();
    mockGenerate.mockRejectedValue(
      new APIError(503, { detail: 'The AI service is unavailable.' }, 'provider_unavailable'),
    );

    renderWorkspace();
    await user.click(await screen.findByRole('button', { name: 'Help me word this' }));

    await user.type(screen.getByLabelText('Prompt description'), 'anything');
    await user.click(screen.getByRole('button', { name: 'Write the prompt' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/could not be reached/i);
    expect(alert).toHaveTextContent(/nothing was charged/i);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});
