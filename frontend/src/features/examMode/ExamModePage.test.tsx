import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { examModeAPI } from '@/api/examMode';
import type { ExamAnalysisResult } from '@/api/types';
import type { Workspace } from '@/data/workspaces';
import { queryCache } from '@/lib/query/cache';
import ExamModePage from './ExamModePage';

const navigate = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

vi.mock('@/api/examMode', () => ({
  examModeAPI: {
    listSources: vi.fn(),
    listPlans: vi.fn(),
    getAnalysis: vi.fn(),
    listEntitlements: vi.fn(),
    analyse: vi.fn(),
    rescan: vi.fn(),
    createPlan: vi.fn(),
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

const currentUser = {
  id: 1,
  name: 'Student',
  email: 's@example.com',
  role: 'user',
  is_banned: false,
  is_email_verified: true,
  credits: null,
  preferred_model: 'ollama:llama3.1',
  education_level: 'unspecified',
};

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({
    user: currentUser,
    isAuthenticated: true,
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
  }),
}));

const listSources = vi.mocked(examModeAPI.listSources);
const listPlans = vi.mocked(examModeAPI.listPlans);
const getAnalysis = vi.mocked(examModeAPI.getAnalysis);
const listEntitlements = vi.mocked(examModeAPI.listEntitlements);
const analyse = vi.mocked(examModeAPI.analyse);

const READY_DOCUMENT = {
  id: 'doc-1',
  label: 'Lecture 1.pdf',
  material_kind: 'lecture_notes',
  status: 'ready',
  is_past_exam: false,
  is_syllabus: false,
};

const INVENTORY = {
  syllabus_present: true,
  syllabus_characters: 900,
  course_topics: ['Photosynthesis'],
  documents: [READY_DOCUMENT],
  ready_document_count: 1,
  past_exam_document_count: 0,
  chunks_available: 24,
};

const TOPIC = {
  topic_key: 'photosynthesis',
  display_label: 'Photosynthesis',
  aliases: [],
  in_syllabus: true,
  in_course_topics: true,
  in_past_exams: false,
  in_material: true,
  discovery_confidence: 0.9,
  syllabus_weight_percent: 30,
  syllabus_mention_count: 3,
  past_exam_question_count: 0,
  material_chunk_count: 8,
  citations: [],
};

const ANALYSIS = {
  generated_output_id: 55,
  created_at: '2026-08-20T10:00:00Z',
  model_used: 'ollama:llama3.1',
  candidate_count: 1,
  past_exam_question_count: 0,
  documents_analysed: ['doc-1'],
  manual_review_recommended: false,
  topics: [TOPIC],
  selection_carry_over: {
    preselected_topic_keys: ['photosynthesis'],
    high_priority_topic_keys: [],
    new_topic_keys: [],
    unsupported_topic_keys: [],
    previous_plan_output_id: null,
  },
  confidence_notes: '',
};

const ANALYSIS_RESULT = {
  analysis: ANALYSIS,
  context_truncated: false,
  chunks_used: 8,
  chunks_available: 24,
  retrieval_narrowed: true,
  lowest_similarity: 0.42,
  highest_similarity: 0.91,
} satisfies ExamAnalysisResult;

async function tickFirstSource(user: ReturnType<typeof userEvent.setup>) {
  const boxes = screen.getAllByRole('checkbox');
  await user.click(boxes[0]);
}

function workspaceFixture(overrides: Partial<Workspace> = {}): Workspace {
  return {
    id: '1',
    name: 'Biology',
    subjectArea: 'Biology',
    educationLevel: 'unspecified',
    semester: 'Fall',
    examDate: '2026-12-01',
    topics: ['Photosynthesis'],
    syllabus: '',
    progress: null,
    updatedAt: '2026-08-20T10:00:00Z',
    accent: 'green',
    ...overrides,
  } as Workspace;
}

function renderPage(workspace: Workspace = workspaceFixture()) {
  return render(
    <MemoryRouter>
      <ExamModePage workspace={workspace} />
    </MemoryRouter>,
  );
}

describe('ExamModePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryCache.clear();
    credits.isMetered = false;
    credits.canAfford = () => true;
    listSources.mockResolvedValue(INVENTORY);
    listPlans.mockResolvedValue({ plans: [], current_plan_output_id: null });
    getAnalysis.mockRejectedValue(new APIError(404, 'Not Found'));
    listEntitlements.mockResolvedValue({ unlocked_topic_keys: [] });
  });

  it('shows a loading state before the course reads settle', () => {
    renderPage();

    expect(screen.getByRole('status', { name: /loading exam mode/i })).toBeInTheDocument();
  });

  it('names the course once loaded', async () => {
    renderPage();

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /Biology Exam Mode/i })).toBeInTheDocument(),
    );
  });

  it('offers a retry when the course reads fail', async () => {
    listSources.mockRejectedValue(new APIError(500, 'nope'));

    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/Exam Mode could not be loaded/i)).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: /try again|retry/i })).toBeInTheDocument();
  });

  it('offers the sources stage to an owner', async () => {
    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/Choose what to read/i)).toBeInTheDocument(),
    );
  });

  it('tells a support reader the view is read-only and offers no generation', async () => {
    renderPage(workspaceFixture({ ownerId: 99, ownerName: 'Ada' } as Partial<Workspace>));

    await waitFor(() =>
      expect(screen.getByText(/Read-only support view/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/Ada/)).toBeInTheDocument();
    expect(screen.queryByText(/Choose what to read/i)).not.toBeInTheDocument();
    expect(listEntitlements).not.toHaveBeenCalled();
  });

  it('warns about an unindexed course instead of telling the student to re-upload', async () => {
    listSources.mockResolvedValue({ ...INVENTORY, chunks_available: 0 });

    renderPage();

    await waitFor(() => expect(screen.getByText(/Choose what to read/i)).toBeInTheDocument());
    expect(document.body.textContent).toMatch(/index/i);
  });

  it('runs an analysis and keeps the returned topic selection', async () => {
    analyse.mockResolvedValue(ANALYSIS_RESULT);
    const user = userEvent.setup();

    renderPage();
    await waitFor(() => expect(screen.getByText(/Choose what to read/i)).toBeInTheDocument());

    await tickFirstSource(user);
    await user.click(screen.getByRole('button', { name: /read these sources/i }));

    await waitFor(() => expect(analyse).toHaveBeenCalledWith(1, { document_ids: ['doc-1'] }));
  });

  it('refuses to spend credits it does not have', async () => {
    credits.isMetered = true;
    credits.canAfford = () => false;
    const user = userEvent.setup();

    renderPage();
    await waitFor(() => expect(screen.getByText(/Choose what to read/i)).toBeInTheDocument());

    await tickFirstSource(user);
    await user.click(screen.getByRole('button', { name: /read these sources/i }));

    expect(analyse).not.toHaveBeenCalled();
    await waitFor(() => expect(document.body.textContent).toMatch(/credit/i));
  });

  it('reports an analysis failure without claiming a result', async () => {
    analyse.mockRejectedValue(new APIError(503, 'provider down'));
    const user = userEvent.setup();

    renderPage();
    await waitFor(() => expect(screen.getByText(/Choose what to read/i)).toBeInTheDocument());

    await tickFirstSource(user);
    await user.click(screen.getByRole('button', { name: /read these sources/i }));

    await waitFor(() => expect(analyse).toHaveBeenCalled());
    await waitFor(() => expect(screen.getAllByRole('alert').length).toBeGreaterThan(0));
  });

  it('lists saved plans when the course already has some', async () => {
    listPlans.mockResolvedValue({
      plans: [
        {
          generated_output_id: 7,
          analysis_output_id: 5,
          plan_version: 1,
          supersedes_output_id: null,
          created_at: '2026-08-21T09:00:00Z',
          exam_date: '2026-12-01',
          topic_count: 4,
          selection_mode: 'manual',
          is_current: true,
        },
      ],
      current_plan_output_id: 7,
    });

    renderPage();

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /your plans/i })).toBeInTheDocument(),
    );
  });
});
