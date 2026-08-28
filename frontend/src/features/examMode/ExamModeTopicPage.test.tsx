import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { examModeAPI } from '@/api/examMode';
import type { ExamPlanView } from '@/api/types';
import type { Workspace } from '@/data/workspaces';
import { queryCache } from '@/lib/query/cache';
import ExamModeTopicPage from './ExamModeTopicPage';

vi.mock('@/api/examMode', () => ({
  examModeAPI: {
    getPlan: vi.fn(),
    getTopicGuide: vi.fn(),
    listEntitlements: vi.fn(),
    generateTopicGuide: vi.fn(),
    generateTopicPractice: vi.fn(),
    generateTopicExam: vi.fn(),
    getSimilarQuestions: vi.fn(),
    generateSimilarQuestions: vi.fn(),
    listPastExamQuestions: vi.fn(),
  },
}));

const credits = {
  status: null,
  isLoading: false,
  error: null,
  refresh: vi.fn(),
  isMetered: false,
  costOf: () => null,
  canAfford: () => true,
};

vi.mock('@/context/CreditContext', () => ({ useCredits: () => credits }));

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 1,
      name: 'Student',
      email: 's@example.com',
      role: 'user',
      is_banned: false,
      is_email_verified: true,
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

const getPlan = vi.mocked(examModeAPI.getPlan);
const getTopicGuide = vi.mocked(examModeAPI.getTopicGuide);
const listEntitlements = vi.mocked(examModeAPI.listEntitlements);
const generateTopicGuide = vi.mocked(examModeAPI.generateTopicGuide);

const TOPIC = {
  topic_key: 'photosynthesis',
  display_label: 'Photosynthesis',
  rank: 1,
  is_high_priority: true,
  priority_score: 82,
  priority_band: 'high',
  has_any_evidence: true,
  is_unattempted: false,
  mastery_percentage: 40,
  signals: {},
  reason_codes: [],
  explanation: 'Weighted highly by syllabus emphasis.',
};

function planFixture(overrides: Partial<ExamPlanView> = {}): ExamPlanView {
  return {
    generated_output_id: 7,
    analysis_output_id: 5,
    plan_version: 1,
    supersedes_output_id: null,
    created_at: '2026-08-20T10:00:00Z',
    exam_date: '2026-12-01',
    days_until_exam: 30,
    selection_mode: 'manual',
    manual_review_recommended: false,
    ranking_engine: 'python',
    ranking_policy_version: 1,
    configured_weights: {},
    effective_weights: {},
    signals_available: {},
    signal_bases: {},
    unmapped_mastery_labels: 0,
    warnings: [],
    topics: [TOPIC],
    staleness: { is_stale: false, requires_rescan: false, stale_reasons: [] },
    ...overrides,
  } as ExamPlanView;
}

const workspace = {
  id: '1',
  name: 'Biology',
  subjectArea: 'Biology',
  educationLevel: 'unspecified',
  semester: 'Fall',
  examDate: '2026-12-01',
  topics: [],
  syllabus: '',
  progress: null,
  updatedAt: '2026-08-20T10:00:00Z',
  accent: 'green',
} as unknown as Workspace;

function renderPage(topicKey = 'photosynthesis', planId = '7') {
  return render(
    <MemoryRouter initialEntries={[`/courses/1/exam-mode/plans/${planId}/topics/${topicKey}`]}>
      <Routes>
        <Route
          path="/courses/:courseId/exam-mode/plans/:planId/topics/:topicKey"
          element={<ExamModeTopicPage workspace={workspace} />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ExamModeTopicPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryCache.clear();
    credits.isMetered = false;
    credits.canAfford = () => true;
    getPlan.mockResolvedValue(planFixture());
    getTopicGuide.mockRejectedValue(new APIError(404, 'Not Found'));
    listEntitlements.mockResolvedValue({ unlocked_topic_keys: ['photosynthesis'] });
  });

  it('shows a loading state while the plan is read', () => {
    renderPage();

    expect(screen.getByRole('status', { name: /loading topic/i })).toBeInTheDocument();
  });

  it('names the topic once the plan arrives', async () => {
    renderPage();

    await waitFor(() =>
      expect(screen.getAllByText(/Photosynthesis/i).length).toBeGreaterThan(0),
    );
  });

  it('refuses a topic the plan never ranked', async () => {
    renderPage('not-a-planned-topic');

    await waitFor(() =>
      expect(screen.getByText(/that topic is not in this plan/i)).toBeInTheDocument(),
    );
  });

  it('offers a retry when the plan cannot be read', async () => {
    getPlan.mockRejectedValue(new APIError(500, 'boom'));

    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/that plan could not be loaded/i)).toBeInTheDocument(),
    );
  });

  it('invites the student to write a guide that does not exist yet', async () => {
    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/no guide for this topic yet/i)).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: /write the guide/i })).toBeInTheDocument();
  });

  it('generates the guide when asked', async () => {
    generateTopicGuide.mockResolvedValue({} as never);
    const user = userEvent.setup();

    renderPage();
    const button = await screen.findByRole('button', { name: /write the guide/i });

    await user.click(button);

    await waitFor(() =>
      expect(generateTopicGuide).toHaveBeenCalledWith(1, 'photosynthesis', {
        plan_output_id: 7,
      }),
    );
  });

  it('reports a generation failure without claiming a guide', async () => {
    generateTopicGuide.mockRejectedValue(new APIError(503, 'provider down'));
    const user = userEvent.setup();

    renderPage();
    const button = await screen.findByRole('button', { name: /write the guide/i });

    await user.click(button);

    await waitFor(() => expect(generateTopicGuide).toHaveBeenCalled());
    await waitFor(() => expect(screen.getAllByRole('alert').length).toBeGreaterThan(0));
  });

  it('shows the guide once one exists', async () => {
    getTopicGuide.mockResolvedValue({
      generated_output_id: 31,
      topic_key: 'photosynthesis',
      content: '## Photosynthesis\n\nLight-dependent reactions occur in the thylakoid.',
      citations: [],
      created_at: '2026-08-21T09:00:00Z',
      model_used: 'ollama:llama3.1',
    } as never);

    renderPage();

    await waitFor(() =>
      expect(screen.queryByText(/no guide for this topic yet/i)).not.toBeInTheDocument(),
    );
  });
});
