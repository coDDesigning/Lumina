import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { quizAPI } from '@/api/quiz';
import type { QuizAttemptResponse, QuizView } from '@/api/types';
import type { Workspace } from '@/data/workspaces';
import QuizResultsPage from './QuizResultsPage';

vi.mock('@/api/quiz', () => ({
  quizAPI: {
    get: vi.fn(),
    getAttempt: vi.fn(),
  },
}));

const mockGet = vi.mocked(quizAPI.get);
const mockGetAttempt = vi.mocked(quizAPI.getAttempt);

const SAMPLE_WORKSPACE: Workspace = {
  id: '10',
  name: 'Linear Algebra',
  subjectArea: 'Mathematics',
  educationLevel: 'undergraduate',
  semester: 'Fall',
  examDate: '2026-12-01',
  topics: ['Matrices', 'Eigenvalues'],
  syllabus: 'Vectors and linear transformations.',
  progress: {
    averageScore: 50,
    timeSpentSeconds: null,
    lastActivity: '2026-08-22T10:00:00Z',
    status: 'practiced',
  },
  updatedAt: 'Updated today',
  accent: 'blue',
};

const SAMPLE_QUIZ: QuizView = {
  quiz_id: 1,
  course_id: 10,
  title: 'Eigenvalues & Eigenvectors',
  created_at: '2026-08-23T10:00:00Z',
  user_id: 1,
  model_used: 'ollama:qwen3:8b',
  generation_settings: null,
  generation_context: null,
  questions: [
    {
      question_id: 101,
      question_number: 1,
      question_type: 'multiple_choice',
      difficulty: 'medium',
      topic: 'Eigenvalues',
      question: 'What is det(A - lambda*I) called?',
      options: [
        'Characteristic polynomial',
        'Minimal polynomial',
        'Eigen polynomial',
        'Trace',
      ],
      correct_option_index: 0,
      correct_answer: { type: 'multiple_choice', option_index: 0 },
      explanation: 'Setting det(A - lambda*I) = 0 gives the characteristic equation.',
    },
  ],
};

const SAMPLE_ATTEMPT: QuizAttemptResponse = {
  attempt_id: 201,
  quiz_id: 1,
  score: 1.0,
  correct_count: 1,
  graded_count: 1,
  total_questions: 1,
  time_spent_seconds: 45,
  created_at: '2026-08-23T10:05:00Z',
  answers: [
    {
      question_id: 101,
      question_type: 'multiple_choice',
      selected_option_index: 0,
      text_response: null,
      correct_option_index: 0,
      correct_answer: { type: 'multiple_choice', option_index: 0 },
      is_correct: true,
      score: 1.0,
      feedback: 'Excellent.',
      time_spent_seconds: 45,
      topic: 'Eigenvalues',
    },
  ],
};

describe('QuizResultsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('cold loads attempt details from route parameters', async () => {
    mockGet.mockResolvedValue(SAMPLE_QUIZ);
    mockGetAttempt.mockResolvedValue(SAMPLE_ATTEMPT);

    render(
      <MemoryRouter initialEntries={['/courses/10/practice/1/attempts/201']}>
        <Routes>
          <Route
            path="/courses/:courseId/practice/:quizId/attempts/:attemptId"
            element={<QuizResultsPage workspace={SAMPLE_WORKSPACE} />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(mockGet).toHaveBeenCalledWith(10, 1, expect.anything());
    expect(mockGetAttempt).toHaveBeenCalledWith(10, 1, 201, expect.anything());

    expect(await screen.findByText('100%')).toBeInTheDocument();
    expect(screen.getByText('What is det(A - lambda*I) called?')).toBeInTheDocument();
    expect(screen.getAllByText('Correct').length).toBeGreaterThanOrEqual(1);
  });

  it('renders directly without fetching when handedIn state is present', () => {
    render(
      <MemoryRouter
        initialEntries={[
          {
            pathname: '/courses/10/practice/1/attempts/201',
            state: { quiz: SAMPLE_QUIZ, attempt: SAMPLE_ATTEMPT },
          },
        ]}
      >
        <Routes>
          <Route
            path="/courses/:courseId/practice/:quizId/attempts/:attemptId"
            element={<QuizResultsPage workspace={SAMPLE_WORKSPACE} />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(mockGet).not.toHaveBeenCalled();
    expect(mockGetAttempt).not.toHaveBeenCalled();

    expect(screen.getByText('100%')).toBeInTheDocument();
    expect(screen.getByText('What is det(A - lambda*I) called?')).toBeInTheDocument();
  });

  it('displays error alert when fetching fails', async () => {
    mockGet.mockRejectedValue(new Error('Quiz not found'));
    mockGetAttempt.mockRejectedValue(new Error('Attempt not found'));

    render(
      <MemoryRouter initialEntries={['/courses/10/practice/1/attempts/201']}>
        <Routes>
          <Route
            path="/courses/:courseId/practice/:quizId/attempts/:attemptId"
            element={<QuizResultsPage workspace={SAMPLE_WORKSPACE} />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText('These results are not here')).toBeInTheDocument();
  });

  it('displays error alert when route parameters are invalid', async () => {
    render(
      <MemoryRouter initialEntries={['/courses/10/practice/invalid/attempts/abc']}>
        <Routes>
          <Route
            path="/courses/:courseId/practice/:quizId/attempts/:attemptId"
            element={<QuizResultsPage workspace={SAMPLE_WORKSPACE} />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText('That is not a valid quiz attempt address.')).toBeInTheDocument();
  });

  it('renders ungraded open-ended answers visibly as Not scored', async () => {
    const openEndedQuiz: QuizView = {
      ...SAMPLE_QUIZ,
      questions: [
        {
          question_id: 102,
          question_number: 1,
          question_type: 'open_ended',
          difficulty: 'hard',
          topic: 'Proof',
          question: 'Prove why eigenvalues of symmetric matrices are real.',
          options: null,
          correct_option_index: null,
          correct_answer: {
            type: 'open_ended',
            reference_answer: 'Using complex conjugates and transpose.',
          },
          explanation: 'Standard proof uses x^* A x.',
        },
      ],
    };

    const ungradedAttempt: QuizAttemptResponse = {
      attempt_id: 202,
      quiz_id: 1,
      score: 0,
      correct_count: 0,
      graded_count: 0,
      total_questions: 1,
      time_spent_seconds: 60,
      created_at: '2026-08-23T10:10:00Z',
      answers: [
        {
          question_id: 102,
          question_type: 'open_ended',
          selected_option_index: null,
          text_response: 'By considering conjugate transpose.',
          correct_option_index: null,
          correct_answer: {
            type: 'open_ended',
            reference_answer: 'Using complex conjugates and transpose.',
          },
          is_correct: null,
          score: null,
          feedback: null,
          time_spent_seconds: 60,
          topic: 'Proof',
        },
      ],
    };

    mockGet.mockResolvedValue(openEndedQuiz);
    mockGetAttempt.mockResolvedValue(ungradedAttempt);

    render(
      <MemoryRouter initialEntries={['/courses/10/practice/1/attempts/202']}>
        <Routes>
          <Route
            path="/courses/:courseId/practice/:quizId/attempts/:attemptId"
            element={<QuizResultsPage workspace={SAMPLE_WORKSPACE} />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText('Prove why eigenvalues of symmetric matrices are real.')).toBeInTheDocument();
    expect(screen.getAllByText('Not scored').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/could not be marked automatically/i)).toBeInTheDocument();
  });
});

