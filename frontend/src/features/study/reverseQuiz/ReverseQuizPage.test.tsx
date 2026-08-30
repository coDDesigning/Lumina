import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { generateReverseQuiz } from '@/api/reverseQuiz';
import type { ReverseQuizResponse } from '@/api/types';
import type { Workspace } from '@/data/workspaces';
import ReverseQuizPage from './ReverseQuizPage';

vi.mock('@/api/reverseQuiz', () => ({
  generateReverseQuiz: vi.fn(),
  getReverseQuizzes: vi.fn(),
}));

const mockGenerateReverseQuiz = vi.mocked(generateReverseQuiz);

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
