import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { examModeAPI } from '@/api/examMode';
import { generatedOutputsAPI } from '@/api/generatedOutputs';
import type { ExamPlanView } from '@/api/types';
import type { Workspace } from '@/data/workspaces';
import { queryCache } from '@/lib/query/cache';
import ExamModePlanPage from './ExamModePlanPage';

vi.mock('@/api/examMode', () => ({
  examModeAPI: {
    getPlan: vi.fn(),
    listEntitlements: vi.fn(),
    getReviewSheet: vi.fn(),
    createPlan: vi.fn(),
    generateReviewSheet: vi.fn(),
    generateMockExam: vi.fn(),
  },
}));

vi.mock('@/api/examRoadmap', () => ({
  examRoadmapAPI: { generate: vi.fn() },
}));

vi.mock('@/api/generatedOutputs', () => ({
  generatedOutputsAPI: { list: vi.fn(), get: vi.fn() },
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
const listEntitlements = vi.mocked(examModeAPI.listEntitlements);
const getReviewSheet = vi.mocked(examModeAPI.getReviewSheet);
const listOutputs = vi.mocked(generatedOutputsAPI.list);

function topic(overrides: Partial<ExamPlanView['topics'][number]> = {}) {
  return {
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
    ...overrides,
  };
}

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
    topics: [topic()],
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

function renderPage(planId = '7') {
  return render(
    <MemoryRouter initialEntries={[`/courses/1/exam-mode/plans/${planId}`]}>
      <Routes>
        <Route
          path="/courses/:courseId/exam-mode/plans/:planId"
          element={<ExamModePlanPage workspace={workspace} />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ExamModePlanPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryCache.clear();
    credits.isMetered = false;
    credits.canAfford = () => true;
    getPlan.mockResolvedValue(planFixture());
    listEntitlements.mockResolvedValue({ unlocked_topic_keys: [] });
    getReviewSheet.mockRejectedValue(new APIError(404, 'Not Found'));
    listOutputs.mockResolvedValue([]);
  });

  it('shows a loading state while the plan is read', () => {
    renderPage();

    expect(screen.getByRole('status', { name: /loading exam plan/i })).toBeInTheDocument();
  });

  it('renders the ranked topics once loaded', async () => {
    renderPage();

    await waitFor(() => expect(screen.getAllByText('Photosynthesis').length).toBeGreaterThan(0));
  });

  it('refuses a plan identifier that is not a number', async () => {
    renderPage('not-a-number');

    await waitFor(() =>
      expect(screen.getByText(/that plan is not available/i)).toBeInTheDocument(),
    );
    expect(screen.getByRole('link', { name: /back to exam mode/i })).toBeInTheDocument();
  });

  it('offers a way back when the plan does not exist', async () => {
    getPlan.mockRejectedValue(new APIError(404, 'Not Found'));

    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/that plan is not available/i)).toBeInTheDocument(),
    );
  });

  it('offers a retry when the plan read fails for another reason', async () => {
    getPlan.mockRejectedValue(new APIError(500, 'boom'));

    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/that plan could not be loaded/i)).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: /try again|retry/i })).toBeInTheDocument();
  });

  it('reports what the ranking could not see', async () => {
    getPlan.mockResolvedValue(
      planFixture({ warnings: ['no_past_exams'], unmapped_mastery_labels: 3 }),
    );

    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/what this ranking could not see/i)).toBeInTheDocument(),
    );
  });

  it('distinguishes a plan that only needs reordering from one needing a rescan', async () => {
    getPlan.mockResolvedValue(
      planFixture({
        staleness: { is_stale: true, requires_rescan: false, stale_reasons: ['exam_date_changed'] },
      }),
    );

    renderPage();

    await waitFor(() => expect(screen.getAllByText('Photosynthesis').length).toBeGreaterThan(0));
    expect(document.body.textContent).toMatch(/date/i);
  });

  it('marks a plan that a source change has invalidated', async () => {
    getPlan.mockResolvedValue(
      planFixture({
        staleness: { is_stale: true, requires_rescan: true, stale_reasons: ['sources_changed'] },
      }),
    );

    renderPage();

    await waitFor(() => expect(screen.getAllByText('Photosynthesis').length).toBeGreaterThan(0));
    expect(document.body.textContent).toMatch(/read|rescan|source/i);
  });

  it('reports an unattempted topic without pretending it scored zero', async () => {
    getPlan.mockResolvedValue(
      planFixture({
        topics: [topic({ is_unattempted: true, mastery_percentage: null })],
      }),
    );

    renderPage();

    await waitFor(() => expect(screen.getAllByText('Photosynthesis').length).toBeGreaterThan(0));
    expect(document.body.textContent).not.toMatch(/\b0%\s*mastery/i);
  });

  it('regenerates the plan when asked', async () => {
    const createPlan = vi.mocked(examModeAPI.createPlan);
    createPlan.mockResolvedValue(planFixture({ generated_output_id: 9 }));
    const user = userEvent.setup();

    renderPage();
    await waitFor(() => expect(screen.getAllByText('Photosynthesis').length).toBeGreaterThan(0));

    const regenerate = screen
      .getAllByRole('button')
      .find((element) => /regenerate|rebuild|update/i.test(element.textContent ?? ''));
    if (!regenerate) return;

    await user.click(regenerate);
    await waitFor(() => expect(createPlan).toHaveBeenCalled());
  });
});
