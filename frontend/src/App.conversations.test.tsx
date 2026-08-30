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

vi.mock('./api/generatedOutputs', () => ({
  generatedOutputsAPI: {
    list: vi.fn().mockResolvedValue([]),
    get: vi.fn(),
  },
}));

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
  progressAPI: { get: vi.fn(), listAll: vi.fn() },
}));

vi.mock('./api/courseQa', () => ({
  courseQaAPI: { ask: vi.fn() },
}));

vi.mock('./api/aiTutor', () => ({
  aiTutorAPI: { ask: vi.fn() },
}));

vi.mock('./api/conversations', () => ({
  conversationsAPI: { list: vi.fn(), get: vi.fn(), delete: vi.fn() },
}));

const mockCourseList = vi.mocked(coursesAPI.list);
const mockDocumentList = vi.mocked(coursesAPI.listDocuments);
const mockProgress = vi.mocked(progressAPI.get);
const mockListProgress = vi.mocked(progressAPI.listAll);
const mockQaAsk = vi.mocked(courseQaAPI.ask);
const mockTutorAsk = vi.mocked(aiTutorAPI.ask);
const mockConversationList = vi.mocked(conversationsAPI.list);
const mockConversationGet = vi.mocked(conversationsAPI.get);
const mockConversationDelete = vi.mocked(conversationsAPI.delete);

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
    <MemoryRouter initialEntries={['/courses/1']}>
      <App />
    </MemoryRouter>,
  );
}

async function sendPrompt(prompt: string) {
  const user = userEvent.setup();
  const input = screen.getByLabelText('Enter prompt');
  await user.clear(input);
  await user.type(input, prompt);
  await user.click(screen.getByRole('button', { name: 'Send' }));
}

describe('Workspace conversations', () => {
  beforeEach(() => {
    localStorage.clear();
    mockCourseList.mockResolvedValue([
      createMockCourse({ id: 1, title: 'Operating Systems' }),
    ]);
    mockDocumentList.mockResolvedValue([]);
    mockProgress.mockResolvedValue({
      status: 'no_documents',
      attempts_count: 0,
      average_score: null,
      topic_mastery: [],
    });
    mockListProgress.mockResolvedValue([]);
    mockConversationList.mockResolvedValue([]);
  });

  it('names the screen with a single top-level heading', async () => {
    renderWorkspace();
    await screen.findByRole('button', { name: 'Add Sources' });

    const headings = screen.getAllByRole('heading', { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent(/Operating Systems/);
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
    expect(screen.getByText(/^Made for you/)).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole('button', { name: 'Past threads' }),
    );
    await userEvent.click(
      await screen.findByRole('button', { name: /Tutoring 88/ }),
    );
    await userEvent.click(
      await screen.findByRole('button', { name: 'Pick this up' }),
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

  it('keeps the sources of a resumed thread', async () => {
    const summary: ConversationSummary = {
      id: 91,
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
          id: 201,
          role: 'user',
          content: 'Teach me scheduling.',
          created_at: '2026-08-20T10:00:00Z',
          citations: [],
        },
        {
          id: 202,
          role: 'assistant',
          content: 'Scheduling decides which ready process runs next. [S1]',
          created_at: '2026-08-20T10:00:01Z',
          citations: [
            {
              key: 'S1',
              document_id: '11111111-1111-1111-1111-111111111111',
              document_label: 'Lecture 4',
              page_start: 12,
              page_end: 12,
            },
          ],
        },
      ],
    };
    mockConversationList.mockResolvedValue([summary]);
    mockConversationGet.mockResolvedValue(detail);

    renderWorkspace();
    await screen.findByRole('button', { name: 'Add Sources' });

    await userEvent.click(screen.getByRole('button', { name: 'Past threads' }));
    await userEvent.click(await screen.findByRole('button', { name: /Tutoring 91/ }));
    await userEvent.click(await screen.findByRole('button', { name: 'Pick this up' }));

    expect(await screen.findByText('Lecture 4 · p. 12')).toBeInTheDocument();
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
      name: /use my study profile/i,
    });
    expect(toggle).not.toBeChecked();
    expect(
      screen.getByText(
        /supporting context\. Your course material stays primary\./i,
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

  it('deletes a conversation from past threads modal and resets active thread if loaded', async () => {
    const summary: ConversationSummary = {
      id: 55,
      course_id: 1,
      user_id: 1,
      conversation_type: 'course_qa',
      preview: 'What is a mutex?',
      message_count: 2,
      created_at: '2026-08-20T10:00:00Z',
      updated_at: '2026-08-20T10:05:00Z',
    };
    const detail: ConversationDetail = {
      ...summary,
      messages: [
        {
          id: 61,
          role: 'user',
          content: 'What is a mutex?',
          created_at: '2026-08-20T10:00:00Z',
        },
        {
          id: 62,
          role: 'assistant',
          content: 'A mutex provides mutual exclusion for critical sections.',
          created_at: '2026-08-20T10:00:01Z',
        },
      ],
    };

    mockConversationList.mockResolvedValue([summary]);
    mockConversationGet.mockResolvedValue(detail);
    mockConversationDelete.mockResolvedValue({ id: 55 });

    renderWorkspace();
    await screen.findByRole('button', { name: 'Add Sources' });

    // Open past threads and resume the conversation
    await userEvent.click(screen.getByRole('button', { name: 'Past threads' }));
    await userEvent.click(await screen.findByRole('button', { name: /Question 55/ }));
    await userEvent.click(await screen.findByRole('button', { name: 'Pick this up' }));

    expect(screen.getByText('What is a mutex?')).toBeInTheDocument();
    expect(
      screen.getByText('A mutex provides mutual exclusion for critical sections.'),
    ).toBeInTheDocument();

    // Reopen past threads and delete the conversation
    await userEvent.click(screen.getByRole('button', { name: 'Past threads' }));
    await userEvent.click(await screen.findByRole('button', { name: /Question 55/ }));
    await userEvent.click(await screen.findByRole('button', { name: 'Remove' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Remove it' }));

    await waitFor(() => expect(mockConversationDelete).toHaveBeenCalledWith(1, 55));
    await userEvent.click(screen.getByRole('button', { name: 'Done' }));

    // Verify active thread was cleared
    await waitFor(() => {
      expect(screen.queryByText('What is a mutex?')).not.toBeInTheDocument();
    });
  }, 15_000);

  it('restores the active thread on reload from stored conversation ID', async () => {
    const summary: ConversationSummary = {
      id: 42,
      course_id: 1,
      user_id: 1,
      conversation_type: 'course_qa',
      preview: 'What is paging?',
      message_count: 2,
      created_at: '2026-08-20T10:00:00Z',
      updated_at: '2026-08-20T10:05:00Z',
    };
    const detail: ConversationDetail = {
      ...summary,
      messages: [
        {
          id: 71,
          role: 'user',
          content: 'What is paging?',
          created_at: '2026-08-20T10:00:00Z',
        },
        {
          id: 72,
          role: 'assistant',
          content: 'Paging is a memory management scheme.',
          created_at: '2026-08-20T10:00:01Z',
        },
      ],
    };

    localStorage.setItem('lumina:course:1:conversation:course_qa', '42');
    mockConversationGet.mockResolvedValue(detail);
    mockQaAsk.mockResolvedValue(
      qaResult('Page tables store the mapping.', 42),
    );

    renderWorkspace();
    await screen.findByRole('button', { name: 'Add Sources' });

    // Thread messages should be restored automatically on mount
    expect(await screen.findByText('What is paging?')).toBeInTheDocument();
    expect(
      screen.getByText('Paging is a memory management scheme.'),
    ).toBeInTheDocument();
    expect(mockConversationGet).toHaveBeenCalledWith(1, 42, expect.anything());

    // Continuing the thread should send the persisted conversation_id
    await sendPrompt('How are page tables involved?');
    await waitFor(() =>
      expect(mockQaAsk).toHaveBeenCalledWith(1, {
        question: 'How are page tables involved?',
        conversation_id: 42,
        use_profile_knowledge: false,
        include_profile_context: false,
      }),
    );
  }, 15_000);

  it('renders a copy button for active assistant responses and copies response text', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
      writable: true,
    });

    mockQaAsk.mockResolvedValue(
      qaResult('Dijkstra finds the shortest path in a weighted graph.', 105),
    );

    renderWorkspace();
    await screen.findByRole('button', { name: 'Add Sources' });
    const input = screen.getByLabelText('Enter prompt');
    await user.type(input, 'How does Dijkstra algorithm work?');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    expect(
      await screen.findByText('Dijkstra finds the shortest path in a weighted graph.'),
    ).toBeInTheDocument();

    const copyBtn = await screen.findByRole('button', { name: 'Copy response' });
    expect(copyBtn).toBeInTheDocument();

    await user.click(copyBtn);

    expect(writeText).toHaveBeenCalledWith(
      'Dijkstra finds the shortest path in a weighted graph.',
    );
    expect(await screen.findByRole('button', { name: 'Copied to clipboard' })).toBeInTheDocument();
  }, 15_000);
});

