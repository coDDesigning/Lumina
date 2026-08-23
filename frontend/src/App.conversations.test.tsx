import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { aiTutorAPI } from './api/aiTutor';
import { conversationsAPI } from './api/conversations';
import { courseQaAPI } from './api/courseQa';
import { coursesAPI } from './api/courses';
import { progressAPI } from './api/progress';
import type {
  AiTutorGenerationResult,
  ConversationDetail,
  ConversationSummary,
  CourseQAGenerationResult,
} from './api/types';
import { createMockCourse } from './test/mocks/api';

// These suites are not about credits; an unmetered account renders no credit UI.
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
  progressAPI: { get: vi.fn() },
}));

vi.mock('./api/courseQa', () => ({
  courseQaAPI: { ask: vi.fn() },
}));

vi.mock('./api/aiTutor', () => ({
  aiTutorAPI: { ask: vi.fn() },
}));

vi.mock('./api/conversations', () => ({
  conversationsAPI: { list: vi.fn(), get: vi.fn() },
}));

const mockCourseList = vi.mocked(coursesAPI.list);
const mockDocumentList = vi.mocked(coursesAPI.listDocuments);
const mockProgress = vi.mocked(progressAPI.get);
const mockQaAsk = vi.mocked(courseQaAPI.ask);
const mockTutorAsk = vi.mocked(aiTutorAPI.ask);
const mockConversationList = vi.mocked(conversationsAPI.list);
const mockConversationGet = vi.mocked(conversationsAPI.get);

function qaResult(
  answer: string,
  conversationId: number,
): CourseQAGenerationResult {
  return {
    answer,
    conversation_id: conversationId,
    context_truncated: false,
    chunks_used: 3,
    chunks_available: 20,
    retrieval_narrowed: true,
    lowest_similarity: 0.72,
    highest_similarity: 0.94,
  };
}

function tutorResult(
  answer: string,
  conversationId: number,
): AiTutorGenerationResult {
  return {
    answer,
    conversation_id: conversationId,
    context_truncated: false,
    chunks_used: 4,
    chunks_available: 20,
    retrieval_narrowed: true,
    lowest_similarity: 0.69,
    highest_similarity: 0.9,
  };
}

function renderWorkspace() {
  return render(
    <MemoryRouter initialEntries={['/workspaces/1']}>
      <App />
    </MemoryRouter>,
  );
}

async function sendPrompt(prompt: string) {
  const user = userEvent.setup();
  const input = screen.getByLabelText('Enter prompt');
  await user.clear(input);
  await user.type(input, prompt);
  await user.click(screen.getByRole('button', { name: 'Submit prompt' }));
}

describe('Workspace conversations', () => {
  beforeEach(() => {
    mockCourseList.mockResolvedValue([
      createMockCourse({ id: 1, title: 'Operating Systems' }),
    ]);
    mockDocumentList.mockResolvedValue([]);
    mockProgress.mockResolvedValue({
      attempts_count: 0,
      average_score: null,
      topic_mastery: [],
    });
    mockConversationList.mockResolvedValue([]);
  });

  it('reuses the returned Q&A ID on turn two and clears it for a new conversation', async () => {
    mockQaAsk
      .mockResolvedValueOnce(qaResult('Virtual memory extends the address space.', 31))
      .mockResolvedValueOnce(qaResult('Paging maps virtual pages to physical frames.', 31))
      .mockResolvedValueOnce(qaResult('A page fault loads a missing page.', 32));

    renderWorkspace();
    await screen.findByRole('button', { name: 'Add Sources' });

    await sendPrompt('What is virtual memory?');
    expect(
      await screen.findByText('Virtual memory extends the address space.'),
    ).toBeInTheDocument();
    expect(mockQaAsk).toHaveBeenNthCalledWith(1, 1, {
      question: 'What is virtual memory?',
      use_profile_knowledge: false,
      include_profile_context: false,
    });

    await sendPrompt('How does paging relate?');
    expect(
      await screen.findByText('Paging maps virtual pages to physical frames.'),
    ).toBeInTheDocument();
    expect(mockQaAsk).toHaveBeenNthCalledWith(2, 1, {
      question: 'How does paging relate?',
      conversation_id: 31,
      use_profile_knowledge: false,
      include_profile_context: false,
    });

    await userEvent.click(
      screen.getByRole('button', { name: 'New conversation' }),
    );
    expect(screen.queryByText('Virtual memory extends the address space.')).not.toBeInTheDocument();

    await sendPrompt('Why does a page fault happen?');
    await screen.findByText('A page fault loads a missing page.');
    expect(mockQaAsk).toHaveBeenNthCalledWith(3, 1, {
      question: 'Why does a page fault happen?',
      use_profile_knowledge: false,
      include_profile_context: false,
    });
  }, 15_000);

  it('keeps Course Q&A and AI Tutor IDs and messages separate', async () => {
    mockQaAsk
      .mockResolvedValueOnce(qaResult('A process owns an address space.', 41))
      .mockResolvedValueOnce(qaResult('Threads share their process address space.', 41));
    mockTutorAsk
      .mockResolvedValueOnce(
        tutorResult('Picture a process as a container for threads.', 72),
      )
      .mockResolvedValueOnce(
        tutorResult('Threads are execution paths inside that container.', 72),
      );

    renderWorkspace();
    await screen.findByRole('button', { name: 'Add Sources' });

    await sendPrompt('What does a process own?');
    await screen.findByText('A process owns an address space.');

    await userEvent.click(screen.getByRole('tab', { name: 'Tutor' }));
    expect(screen.queryByText('A process owns an address space.')).not.toBeInTheDocument();
    await sendPrompt('Teach me the process model.');
    await screen.findByText('Picture a process as a container for threads.');
    expect(mockTutorAsk).toHaveBeenCalledWith(1, {
      question: 'Teach me the process model.',
      use_profile_knowledge: false,
      include_profile_context: false,
    });

    await sendPrompt('How do threads fit into that model?');
    await screen.findByText('Threads are execution paths inside that container.');
    expect(mockTutorAsk).toHaveBeenNthCalledWith(2, 1, {
      question: 'How do threads fit into that model?',
      conversation_id: 72,
      use_profile_knowledge: false,
      include_profile_context: false,
    });

    await userEvent.click(screen.getByRole('tab', { name: 'Ask' }));
    expect(screen.getByText('A process owns an address space.')).toBeInTheDocument();
    expect(
      screen.queryByText('Picture a process as a container for threads.'),
    ).not.toBeInTheDocument();

    await sendPrompt('What do threads share?');
    await screen.findByText('Threads share their process address space.');
    expect(mockQaAsk).toHaveBeenNthCalledWith(2, 1, {
      question: 'What do threads share?',
      conversation_id: 41,
      use_profile_knowledge: false,
      include_profile_context: false,
    });
  }, 15_000);

  it('loads and resumes a typed history thread in its matching mode', async () => {
    const summary: ConversationSummary = {
      id: 88,
      course_id: 1,
      user_id: 1,
      conversation_type: 'ai_tutor',
      preview: 'Teach me scheduling.',
      message_count: 2,
      created_at: '2026-08-20T10:00:00Z',
      updated_at: '2026-08-20T10:05:00Z',
    };
    const detail: ConversationDetail = {
      ...summary,
      messages: [
        {
          id: 101,
          role: 'user',
          content: 'Teach me scheduling.',
          created_at: '2026-08-20T10:00:00Z',
        },
        {
          id: 102,
          role: 'assistant',
          content: 'Scheduling decides which ready process runs next.',
          created_at: '2026-08-20T10:00:01Z',
        },
      ],
    };
    mockConversationList.mockResolvedValue([summary]);
    mockConversationGet.mockResolvedValue(detail);
    mockTutorAsk.mockResolvedValue(
      tutorResult('Round robin rotates through ready processes.', 88),
    );

    renderWorkspace();
    await screen.findByRole('button', { name: 'Add Sources' });
    expect(
      screen.getByRole('button', { name: 'Made for you' }),
    ).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole('button', { name: 'Past threads' }),
    );
    await userEvent.click(
      await screen.findByRole('button', { name: /Conversation 88/ }),
    );
    await userEvent.click(
      await screen.findByRole('button', { name: 'Resume conversation' }),
    );

    expect(screen.getByRole('tab', { name: 'Tutor' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByText('Teach me scheduling.')).toBeInTheDocument();
    expect(
      screen.getByText('Scheduling decides which ready process runs next.'),
    ).toBeInTheDocument();

    await sendPrompt('How does round robin work?');
    await waitFor(() =>
      expect(mockTutorAsk).toHaveBeenCalledWith(1, {
        question: 'How does round robin work?',
        conversation_id: 88,
        use_profile_knowledge: false,
        include_profile_context: false,
      }),
    );
  }, 15_000);

  it('restores the question when generation fails', async () => {
    mockQaAsk.mockRejectedValue(new Error('Provider unavailable'));

    renderWorkspace();
    await screen.findByRole('button', { name: 'Add Sources' });
    await sendPrompt('Explain virtual memory.');

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Failed to generate answer from course materials.',
    );
    expect(screen.getByRole('textbox', { name: 'Enter prompt' })).toHaveValue(
      'Explain virtual memory.',
    );
  }, 15_000);

  it('sends questions containing summary, quiz, or Turkish keywords directly to chat without unexpected modal redirection', async () => {
    mockQaAsk.mockResolvedValue(
      qaResult('Here is a summary of the main points.', 99),
    );

    renderWorkspace();
    await screen.findByRole('button', { name: 'Add Sources' });

    await sendPrompt('Please summarize the key algorithms and quiz me.');

    await waitFor(() =>
      expect(mockQaAsk).toHaveBeenCalledWith(1, {
        question: 'Please summarize the key algorithms and quiz me.',
        use_profile_knowledge: false,
        include_profile_context: false,
      }),
    );
    expect(
      await screen.findByText('Here is a summary of the main points.'),
    ).toBeInTheDocument();
  }, 15_000);

  it('toggles profile context opt-in and sends use_profile_knowledge: true when enabled', async () => {
    mockQaAsk.mockResolvedValue(
      qaResult('Here is a personalized explanation.', 101),
    );

    renderWorkspace();
    await screen.findByRole('button', { name: 'Add Sources' });

    const toggle = screen.getByRole('checkbox', {
      name: /include personal study profile context/i,
    });
    expect(toggle).not.toBeChecked();
    expect(
      screen.getByText(
        /Includes your profile background as supplementary context\. Course material remains primary and authoritative\./i,
      ),
    ).toBeInTheDocument();

    await userEvent.click(toggle);
    expect(toggle).toBeChecked();

    await sendPrompt('Explain deadlock conditions.');

    await waitFor(() =>
      expect(mockQaAsk).toHaveBeenCalledWith(1, {
        question: 'Explain deadlock conditions.',
        use_profile_knowledge: true,
        include_profile_context: true,
      }),
    );
    expect(
      await screen.findByText('Here is a personalized explanation.'),
    ).toBeInTheDocument();
  }, 15_000);
});

