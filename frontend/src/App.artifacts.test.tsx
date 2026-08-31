import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { coursesAPI } from './api/courses';
import { flashcardsAPI } from './api/flashcards';
import { generatedOutputsAPI } from './api/generatedOutputs';
import { progressAPI } from './api/progress';
import { quizAPI } from './api/quiz';
import { studyGuideAPI } from './api/studyGuide';
import {
  createMockCourse,
  createMockDocument,
} from './test/mocks/api';

vi.mock('./api/generatedOutputs', () => ({
  generatedOutputsAPI: { list: vi.fn(), get: vi.fn() },
}));

vi.mock('./api/studyGuide', () => ({
  studyGuideAPI: { generate: vi.fn(), enqueue: vi.fn() },
}));
vi.mock('./api/flashcards', () => ({ flashcardsAPI: { enqueue: vi.fn() } }));
vi.mock('./api/quiz', () => ({
  quizAPI: {
    generate: vi.fn(),
    enqueue: vi.fn(),
    list: vi.fn(),
    get: vi.fn(),
    submitAttempt: vi.fn(),
  },
}));

vi.mock('./api/generationJobs', () => ({
  generationJobsAPI: { list: vi.fn().mockResolvedValue([]), get: vi.fn(), retry: vi.fn() },
}));

vi.mock('./api/settings', () => ({
  settingsAPI: {
    get: vi.fn().mockResolvedValue({ difficulty: 'medium', question_count: 5 }),
    update: vi.fn(),
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
}));

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

vi.mock('./api/progress', () => ({ progressAPI: { get: vi.fn(), listAll: vi.fn() } }));
vi.mock('./api/courseQa', () => ({ courseQaAPI: { ask: vi.fn() } }));
vi.mock('./api/aiTutor', () => ({ aiTutorAPI: { ask: vi.fn() } }));
vi.mock('./api/conversations', () => ({
  conversationsAPI: { list: vi.fn(), get: vi.fn(), delete: vi.fn() },
}));

const mockCourseList = vi.mocked(coursesAPI.list);
const mockDocumentList = vi.mocked(coursesAPI.listDocuments);
const mockProgress = vi.mocked(progressAPI.get);
const mockListProgress = vi.mocked(progressAPI.listAll);
const mockOutputList = vi.mocked(generatedOutputsAPI.list);
const mockOutputGet = vi.mocked(generatedOutputsAPI.get);
const mockQuizEnqueue = vi.mocked(quizAPI.enqueue);
const mockFlashcardEnqueue = vi.mocked(flashcardsAPI.enqueue);
const mockStudyGuideEnqueue = vi.mocked(studyGuideAPI.enqueue);

function renderWorkspace(initialEntry = '/courses/1') {
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <App />
    </MemoryRouter>,
  );
  return userEvent.setup();
}

function mockSavedStudyGuide() {
  const summary = {
    id: 12,
    course_id: 1,
    output_type: 'study_guide',
    user_id: 1,
    model_used: 'gemini:gemini-3.6-flash',
    created_at: '2026-08-28T10:00:00Z',
    generation_settings: null,
    generation_context: null,
  };
  mockOutputList.mockResolvedValue([summary]);
  mockOutputGet.mockResolvedValue({
    ...summary,
    content: {
      title: 'Stored Paging Guide',
      summary: 'This saved guide opens without replacing the conversation.',
      key_points: [],
      important_terms: [],
      common_mistakes: [],
      exam_tips: { lecture_based: [], ai_suggestions: [] },
      difficulty: { level: 'Medium', reason: 'Address translation has several steps.' },
      estimated_study_time: '30 minutes',
      prerequisites: [],
      learning_objectives: [],
      coverage: { status: 'Partial', estimated_completeness: 60 },
      confidence_notes: '',
    },
  });
}

beforeEach(() => {
  localStorage.clear();
  mockCourseList.mockResolvedValue([
    createMockCourse({
      id: 1,
      title: 'Operating Systems',
      topics: ['Paging', 'Scheduling'],
    }),
  ]);
  mockDocumentList.mockResolvedValue([createMockDocument({ status: 'ready' })]);
  mockProgress.mockResolvedValue({
    status: 'ready',
    attempts_count: 0,
    average_score: null,
    topic_mastery: [],
  });
  mockListProgress.mockResolvedValue([]);
  mockOutputList.mockResolvedValue([]);
});

describe('making something from the course', () => {
  it('opens the quiz setup from the course page', async () => {
    const person = renderWorkspace();

    await person.click(await screen.findByRole('button', { name: 'Practice quiz' }));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveAccessibleName(/Practice quiz/);
    expect(await screen.findByRole('button', { name: /start the quiz/i })).toBeInTheDocument();
  });

  it('opens the flashcard setup from the course page', async () => {
    const person = renderWorkspace();

    await person.click(await screen.findByRole('button', { name: 'Flashcards' }));

    expect(await screen.findByRole('button', { name: /make flashcards/i })).toBeInTheDocument();
  });

  it('opens the study guide setup from the course page', async () => {
    const person = renderWorkspace();

    await person.click(await screen.findByRole('button', { name: 'Study guide' }));

    expect(
      await screen.findByRole('button', { name: /write my study guide/i }),
    ).toBeInTheDocument();
  });

  it('queues a study guide without replacing the current conversation', async () => {
    mockStudyGuideEnqueue.mockResolvedValue({ job_id: 12, status: 'queued' });
    const person = renderWorkspace();

    await person.click(await screen.findByRole('button', { name: 'Study guide' }));
    await person.click(await screen.findByRole('button', { name: /write my study guide/i }));

    await waitFor(() => expect(mockStudyGuideEnqueue).toHaveBeenCalled());
    expect(screen.queryByRole('dialog', { name: 'Study guide' })).not.toBeInTheDocument();
    expect(
      screen.getByPlaceholderText(/Ask anything about Operating Systems/),
    ).toBeInTheDocument();
  });

  it('opens a saved study guide over the current conversation', async () => {
    mockSavedStudyGuide();
    const person = renderWorkspace();

    await person.click(
      await screen.findByRole('button', { name: /Study guide Whole course/ }),
    );

    expect(
      await screen.findByText('This saved guide opens without replacing the conversation.'),
    ).toBeInTheDocument();
    // The guide itself opens, not the list it was picked from.
    expect(screen.getByRole('dialog')).toHaveAccessibleName('Study guide');
    expect(
      screen.getByPlaceholderText(/Ask anything about Operating Systems/),
    ).toBeInTheDocument();
  });

  it('opens an old study guide address over the course conversation', async () => {
    mockSavedStudyGuide();
    renderWorkspace('/courses/1/guides/12');

    expect(
      await screen.findByText('This saved guide opens without replacing the conversation.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toHaveAccessibleName('Made for you');
    expect(
      screen.getByPlaceholderText(/Ask anything about Operating Systems/),
    ).toBeInTheDocument();
  });

  it('offers the course topics to the quiz it opens', async () => {
    const person = renderWorkspace();

    await person.click(await screen.findByRole('button', { name: 'Practice quiz' }));

    const select = await screen.findByLabelText(/Which topic/);
    expect(select).toHaveTextContent('Paging');
    expect(select).toHaveTextContent('Scheduling');
  });

  it('queues the quiz in the background', async () => {
    mockQuizEnqueue.mockResolvedValue({ job_id: 42, status: 'queued' });
    const person = renderWorkspace();

    await person.click(await screen.findByRole('button', { name: 'Practice quiz' }));
    await person.click(await screen.findByRole('button', { name: /start the quiz/i }));

    await waitFor(() => expect(mockQuizEnqueue).toHaveBeenCalled());
    expect(mockQuizEnqueue.mock.calls[0][0]).toBe(1);
  });

  it('queues a deck in the background instead of holding the page for it', async () => {
    mockFlashcardEnqueue.mockResolvedValue({ job_id: 9, status: 'queued' });
    const person = renderWorkspace();

    await person.click(await screen.findByRole('button', { name: 'Flashcards' }));
    await person.click(await screen.findByRole('button', { name: /make flashcards/i }));

    await waitFor(() => expect(mockFlashcardEnqueue).toHaveBeenCalled());
    expect(mockFlashcardEnqueue.mock.calls[0][0]).toBe(1);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('leaves the course page alone when the student backs out of a modal', async () => {
    const person = renderWorkspace();

    await person.click(await screen.findByRole('button', { name: 'Flashcards' }));
    await screen.findByRole('button', { name: /make flashcards/i });
    await person.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.getByRole('button', { name: 'Flashcards' })).toBeInTheDocument();
    expect(mockStudyGuideEnqueue).not.toHaveBeenCalled();
  });
});

describe('when nothing is ready to generate from', () => {
  it('says so rather than offering a generation that cannot work', async () => {
    mockDocumentList.mockResolvedValue([]);
    const person = renderWorkspace();

    expect(await screen.findByText(/Nothing is ready to generate from yet/)).toBeInTheDocument();

    await person.click(screen.getByRole('button', { name: 'Practice quiz' }));

    expect(await screen.findByText('There is nothing to work from yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /start the quiz/i })).toBeDisabled();
  });
});
