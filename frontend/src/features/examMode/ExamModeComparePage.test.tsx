import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { examModeAPI } from '@/api/examMode';
import type { ExamPlanView } from '@/api/types';
import type { Workspace } from '@/data/workspaces';
import { queryCache } from '@/lib/query/cache';
import ExamModeComparePage from './ExamModeComparePage';

vi.mock('@/api/examMode', () => ({
  examModeAPI: { getPlan: vi.fn() },
}));

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

function topic(overrides: Record<string, unknown> = {}) {
  return {
    topic_key: 'photosynthesis',
    display_label: 'Photosynthesis',
    rank: 1,
    is_high_priority: false,
    priority_score: 80,
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

function renderPage(planId = '7', otherId = '8') {
  return render(
    <MemoryRouter
      initialEntries={[`/courses/1/exam-mode/plans/${planId}/compare/${otherId}`]}
    >
      <Routes>
        <Route
          path="/courses/:courseId/exam-mode/plans/:planId/compare/:otherPlanId"
          element={<ExamModeComparePage workspace={workspace} />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ExamModeComparePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryCache.clear();
    getPlan.mockImplementation((_courseId: number, planId: number) =>
      Promise.resolve(
        planId === 7
          ? planFixture()
          : planFixture({
              generated_output_id: 8,
              plan_version: 2,
              topics: [topic({ rank: 2, priority_score: 60, mastery_percentage: 75 })],
            }),
      ),
    );
  });

  it('shows a loading state while both plans are read', () => {
    renderPage();

    expect(screen.getByRole('status', { name: /loading comparison/i })).toBeInTheDocument();
  });

  it('refuses plan identifiers that are not numbers', async () => {
    renderPage('nope', 'also-nope');

    await waitFor(() =>
      expect(screen.getByText(/those plans are not available/i)).toBeInTheDocument(),
    );
  });

  it('reports a missing plan rather than a broken screen', async () => {
    getPlan.mockRejectedValue(new APIError(404, 'Not Found'));

    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/those plans are not available/i)).toBeInTheDocument(),
    );
  });

  it('offers a retry when a plan read fails for another reason', async () => {
    getPlan.mockRejectedValue(new APIError(500, 'boom'));

    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/the comparison could not be loaded/i)).toBeInTheDocument(),
    );
  });

  it('reads both plans of the comparison', async () => {
    renderPage();

    await waitFor(() => expect(getPlan).toHaveBeenCalledTimes(2));
    expect(getPlan).toHaveBeenCalledWith(1, 7, expect.anything());
    expect(getPlan).toHaveBeenCalledWith(1, 8, expect.anything());
  });

  it('shows what changed between the two plans', async () => {
    renderPage();

    await waitFor(() =>
      expect(screen.getAllByText(/Photosynthesis/i).length).toBeGreaterThan(0),
    );
    expect(screen.getAllByLabelText('becomes').length).toBeGreaterThan(0);
  });

  it('does not invent a mastery change for an unattempted topic', async () => {
    getPlan.mockImplementation((_courseId: number, planId: number) =>
      Promise.resolve(
        planFixture({
          generated_output_id: planId,
          topics: [
            topic({
              rank: planId === 7 ? 1 : 3,
              is_unattempted: true,
              mastery_percentage: null,
            }),
          ],
        }),
      ),
    );

    renderPage();

    await waitFor(() =>
      expect(screen.getAllByText(/Photosynthesis/i).length).toBeGreaterThan(0),
    );
    expect(document.body.textContent).not.toMatch(/\b0%\b/);
  });
});
