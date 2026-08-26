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
import { createMockCourse, createMockDocument } from './test/mocks/api';

vi.mock('./api/generatedOutputs', () => ({
  generatedOutputsAPI: { list: vi.fn(), get: vi.fn() },
}));

vi.mock('./api/studyGuide', () => ({ studyGuideAPI: { generate: vi.fn() } }));
vi.mock('./api/flashcards', () => ({ flashcardsAPI: { generate: vi.fn() } }));
vi.mock('./api/quiz', () => ({
  quizAPI: { generate: vi.fn(), list: vi.fn(), get: vi.fn(), submitAttempt: vi.fn() },
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
const mockQuizGenerate = vi.mocked(quizAPI.generate);
const mockFlashcards = vi.mocked(flashcardsAPI.generate);
const mockStudyGuide = vi.mocked(studyGuideAPI.generate);

function renderWorkspace() {
  render(
    <MemoryRouter initialEntries={['/courses/1']}>
      <App />
    </MemoryRouter>,
  );
  return userEvent.setup();
}

beforeEach(() => {
  localStorage.clear();
  mockCourseList.mockResolvedValue([
    createMockCourse({ id: 1, title: 'Operating Systems', topics: 'Paging, Scheduling' }),
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

  it('offers the course topics to the quiz it opens', async () => {
    const person = renderWorkspace();

    await person.click(await screen.findByRole('button', { name: 'Practice quiz' }));

    const select = await screen.findByLabelText(/Which topic/);
    expect(select).toHaveTextContent('Paging');
    expect(select).toHaveTextContent('Scheduling');
  });

  it('takes the student to the quiz once it has been written', async () => {
    mockQuizGenerate.mockResolvedValue({
      quiz: { quiz_id: 42, course_id: 1, questions: [] },
    } as never);
    const person = renderWorkspace();

    await person.click(await screen.findByRole('button', { name: 'Practice quiz' }));
    await person.click(await screen.findByRole('button', { name: /start the quiz/i }));

    await waitFor(() => expect(mockQuizGenerate).toHaveBeenCalled());
    expect(mockQuizGenerate.mock.calls[0][0]).toBe(1);
  });

  it('refreshes what the course has made after a deck is generated', async () => {
    mockFlashcards.mockResolvedValue({
      flashcards: {
        deck_title: 'Paging cards',
        card_count: 1,
        flashcards: [
          { card_number: 1, front: 'What is a page?', back: 'A fixed block.', difficulty: 'Easy' },
        ],
      },
      generated_output_id: 9,
      context_truncated: false,
      chunks_used: 2,
      chunks_available: 5,
      retrieval_narrowed: false,
      lowest_similarity: null,
      highest_similarity: null,
    });
    const person = renderWorkspace();

    await person.click(await screen.findByRole('button', { name: 'Flashcards' }));
    await person.click(await screen.findByRole('button', { name: /make flashcards/i }));

    expect(await screen.findByText('What is a page?')).toBeInTheDocument();
    await waitFor(() => expect(mockOutputList.mock.calls.length).toBeGreaterThan(1));
  });

  it('leaves the course page alone when the student backs out of a modal', async () => {
    const person = renderWorkspace();

    await person.click(await screen.findByRole('button', { name: 'Flashcards' }));
    await screen.findByRole('button', { name: /make flashcards/i });
    await person.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.getByRole('button', { name: 'Flashcards' })).toBeInTheDocument();
    expect(mockStudyGuide).not.toHaveBeenCalled();
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
