import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { generateReverseQuiz, suggestReverseQuizQuestions } from '@/api/reverseQuiz';
import type { ReverseQuizResponse } from '@/api/types';
import type { Workspace } from '@/data/workspaces';
import ReverseQuizPage from './ReverseQuizPage';

vi.mock('@/api/reverseQuiz', () => ({
  generateReverseQuiz: vi.fn(),
  suggestReverseQuizQuestions: vi.fn(),
  getReverseQuizzes: vi.fn(),
}));

const refreshCredits = vi.fn().mockResolvedValue(undefined);
const credits = {
  isMetered: true,
  canAfford: vi.fn(() => true),
  refresh: refreshCredits,
  status: {
    credits: 10,
    generation_costs: { reverse_quiz: 1 },
  },
  isLoading: false,
  error: null,
  costOf: vi.fn(() => 1),
};

vi.mock('@/context/CreditContext', () => ({ useCredits: () => credits }));

const mockGenerateReverseQuiz = vi.mocked(generateReverseQuiz);
const mockSuggestQuestions = vi.mocked(suggestReverseQuizQuestions);

const WORKSPACE: Workspace = {
  id: '10',
  name: 'Linear Algebra',
  subjectArea: 'Mathematics',
  educationLevel: 'undergraduate',
  semester: 'Fall',
  examDate: '2026-12-01',
  topics: ['Eigenvalues', 'Matrix Decomposition'],
  syllabus: 'Vectors and matrices.',
  progress: {
    averageScore: 75,
    timeSpentSeconds: 1200,
    lastActivity: '2026-08-30T10:00:00Z',
    status: 'practiced',
  },
  updatedAt: 'Updated today',
  accent: 'blue',
};

const SAMPLE_RESPONSE: ReverseQuizResponse = {
  id: 1,
  course_id: 10,
  topic: 'Eigenvalues',
  explanation: 'Eigenvalues represent scalar factor of eigenvectors.',
  feedback: 'Good general explanation with key concepts captured.',
  misconceptions: [
    {
      concept: 'Zero Eigenvalue',
      detail: 'A zero eigenvalue is valid and indicates a non-invertible matrix.',
      status: 'partially_correct',
    },
  ],
  citations: [],
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/courses/10/reverse-quiz']}>
      <Routes>
        <Route
          path="/courses/:courseId/reverse-quiz"
          element={<ReverseQuizPage workspace={WORKSPACE} />}
        />
        <Route path="/courses/:courseId" element={<p>Course Workspace</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ReverseQuizPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    credits.canAfford.mockReturnValue(true);
    credits.status.credits = 10;
  });

  it('renders page header and course topics', () => {
    renderPage();

    expect(screen.getByRole('link', { name: 'Linear Algebra' })).toBeInTheDocument();
    expect(screen.getByText('Reverse Quiz')).toBeInTheDocument();
    expect(screen.getByText('What would you like to explain today?')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Eigenvalues' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Matrix Decomposition' })).toBeInTheDocument();
  });

  it('starts a reverse quiz session when a topic button is clicked', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole('button', { name: 'Eigenvalues' }));

    expect(screen.getByRole('heading', { level: 2, name: 'Explain: Eigenvalues' })).toBeInTheDocument();
    expect(screen.getByRole('textbox')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Submit Explanation' })).toBeInTheDocument();
  });

  it('blocks both paid actions when the server balance cannot cover them', async () => {
    const user = userEvent.setup();
    credits.canAfford.mockReturnValue(false);
    credits.status.credits = 0;
    renderPage();

    expect(screen.getByRole('button', { name: 'Suggest questions' })).toBeDisabled();
    expect(screen.getByText(/enough credits to generate reverse quiz questions/i)).toBeVisible();

    await user.click(screen.getByRole('button', { name: 'Eigenvalues' }));
    await user.type(screen.getByRole('textbox'), 'Eigenvalues scale eigenvectors.');

    expect(screen.getByRole('button', { name: 'Submit Explanation' })).toBeDisabled();
    expect(screen.getByText(/enough credits to generate this explanation/i)).toBeVisible();
    expect(mockSuggestQuestions).not.toHaveBeenCalled();
    expect(mockGenerateReverseQuiz).not.toHaveBeenCalled();
  });

  it('starts a reverse quiz session with custom topic input', async () => {
    const user = userEvent.setup();
    renderPage();

    const input = screen.getByPlaceholderText("e.g. Photosynthesis, Newton's Laws");
    await user.type(input, 'Singular Value Decomposition');
    await user.click(screen.getByRole('button', { name: 'Start' }));

    expect(
      screen.getByRole('heading', { level: 2, name: 'Explain: Singular Value Decomposition' }),
    ).toBeInTheDocument();
  });

  it('submits explanation and displays feedback and misconceptions', async () => {
    const user = userEvent.setup();
    mockGenerateReverseQuiz.mockResolvedValueOnce(SAMPLE_RESPONSE);

    renderPage();

    await user.click(screen.getByRole('button', { name: 'Eigenvalues' }));

    const textarea = screen.getByRole('textbox');
    await user.type(textarea, 'Eigenvalues represent scalar factor of eigenvectors.');
    await user.click(screen.getByRole('button', { name: 'Submit Explanation' }));

    await waitFor(() => {
      expect(screen.getByText('Analysis for: Eigenvalues')).toBeInTheDocument();
    });

    expect(screen.getByText('Good general explanation with key concepts captured.')).toBeInTheDocument();
    expect(screen.getByText('Zero Eigenvalue')).toBeInTheDocument();
    expect(
      screen.getByText('A zero eigenvalue is valid and indicates a non-invertible matrix.'),
    ).toBeInTheDocument();
  });

  it('resolves a cited marker into its source instead of printing the key', async () => {
    // The feedback keeps its [S1] markers; without the citations beside them
    // the student is left reading a key that names nothing.
    const user = userEvent.setup();
    mockGenerateReverseQuiz.mockResolvedValueOnce({
      ...SAMPLE_RESPONSE,
      feedback: 'Soil is not the source [S1].',
      misconceptions: [
        {
          concept: 'Plant nutrition',
          detail: 'The material says light [S1].',
          status: 'contradicted',
        },
      ],
      citations: [
        {
          key: 'S1',
          document_id: '11111111-1111-1111-1111-111111111111',
          document_label: 'Lecture 4',
          page_start: 12,
          page_end: 12,
        },
      ],
    });

    renderPage();

    await user.click(screen.getByRole('button', { name: 'Eigenvalues' }));
    await user.type(screen.getByRole('textbox'), 'Plants eat soil.');
    await user.click(screen.getByRole('button', { name: 'Submit Explanation' }));

    await waitFor(() => {
      expect(screen.getByText('Analysis for: Eigenvalues')).toBeInTheDocument();
    });

    expect(screen.getAllByText(/Lecture 4/).length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText(/\[S1\]/)).not.toBeInTheDocument();
  });

  it('names a provider outage and retries without a retype', async () => {
    const user = userEvent.setup();
    mockGenerateReverseQuiz
      .mockRejectedValueOnce(new APIError(503, { detail: 'down' }, 'provider_unavailable'))
      .mockResolvedValueOnce(SAMPLE_RESPONSE);

    renderPage();

    await user.click(screen.getByRole('button', { name: 'Eigenvalues' }));
    await user.type(screen.getByRole('textbox'), 'Eigenvalues scale eigenvectors.');
    await user.click(screen.getByRole('button', { name: 'Submit Explanation' }));

    expect(await screen.findByText('The AI service is down')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /try again/i }));

    await waitFor(() => {
      expect(screen.getByText('Analysis for: Eigenvalues')).toBeInTheDocument();
    });
    expect(mockGenerateReverseQuiz).toHaveBeenCalledTimes(2);
  });

  it('suggests source-derived questions and starts a session from one', async () => {
    const user = userEvent.setup();
    mockSuggestQuestions.mockResolvedValueOnce({
      course_id: 10,
      questions: [
        {
          topic: 'Eigenvalue equation',
          question: 'Explain in your own words what Av = λv means geometrically.',
        },
        {
          topic: 'Diagonalisation',
          question: 'Describe when a matrix can be diagonalised.',
        },
      ],
    });
    mockGenerateReverseQuiz.mockResolvedValueOnce({
      ...SAMPLE_RESPONSE,
      topic: 'Eigenvalue equation',
      question: 'Explain in your own words what Av = λv means geometrically.',
    });

    renderPage();

    await user.click(screen.getByRole('button', { name: 'Suggest questions' }));

    const card = await screen.findByRole('button', {
      name: /what Av = λv means geometrically/i,
    });
    await user.click(card);

    expect(
      screen.getByRole('heading', { level: 2, name: 'Explain: Eigenvalue equation' }),
    ).toBeInTheDocument();

    await user.type(screen.getByRole('textbox'), 'It scales the vector by lambda.');
    await user.click(screen.getByRole('button', { name: 'Submit Explanation' }));

    await waitFor(() => expect(mockGenerateReverseQuiz).toHaveBeenCalled());
    expect(mockGenerateReverseQuiz.mock.calls[0][1]).toMatchObject({
      topic: 'Eigenvalue equation',
      question: 'Explain in your own words what Av = λv means geometrically.',
    });
  });

  it('explains a failure when questions cannot be drafted', async () => {
    const user = userEvent.setup();
    mockSuggestQuestions.mockRejectedValueOnce(
      new APIError(503, { detail: 'down' }, 'provider_unavailable'),
    );

    renderPage();

    await user.click(screen.getByRole('button', { name: 'Suggest questions' }));

    expect(await screen.findByText('The AI service is down')).toBeInTheDocument();
  });

  it('does not offer retry when the account cannot afford the action after failure', async () => {
    const user = userEvent.setup();
    mockGenerateReverseQuiz.mockRejectedValueOnce(
      new APIError(503, { detail: 'down' }, 'provider_unavailable'),
    );

    renderPage();

    await user.click(screen.getByRole('button', { name: 'Eigenvalues' }));
    await user.type(screen.getByRole('textbox'), 'Eigenvalues scale eigenvectors.');
    await user.click(screen.getByRole('button', { name: 'Submit Explanation' }));

    expect(await screen.findByText('The AI service is down')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();

    credits.canAfford.mockReturnValue(false);
    credits.status.credits = 0;
    credits.canAfford.mockReturnValue(false);
    await user.type(screen.getByRole('textbox'), ' more');

    expect(screen.queryByRole('button', { name: /try again/i })).not.toBeInTheDocument();
  });

  it('allows restarting to explain another topic', async () => {
    const user = userEvent.setup();
    mockGenerateReverseQuiz.mockResolvedValueOnce(SAMPLE_RESPONSE);

    renderPage();

    await user.click(screen.getByRole('button', { name: 'Eigenvalues' }));

    const textarea = screen.getByRole('textbox');
    await user.type(textarea, 'My explanation text here.');
    await user.click(screen.getByRole('button', { name: 'Submit Explanation' }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Explain Another Topic' })).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: 'Explain Another Topic' }));

    expect(screen.getByText('What would you like to explain today?')).toBeInTheDocument();
  });
});
