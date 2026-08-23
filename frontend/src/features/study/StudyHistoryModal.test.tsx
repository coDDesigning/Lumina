import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { generatedOutputsAPI } from '@/api/generatedOutputs';
import type { GeneratedOutputDetail, GeneratedOutputSummary } from '@/api/types';
import { StudyHistoryModal } from './StudyHistoryModal';

vi.mock('@/api/generatedOutputs', () => ({
  generatedOutputsAPI: { list: vi.fn(), get: vi.fn() },
}));

const mockList = vi.mocked(generatedOutputsAPI.list);
const mockGet = vi.mocked(generatedOutputsAPI.get);

const STUDY_GUIDE_CONTENT = {
  title: 'Stored Guide',
  summary: 'Stored summary',
  key_points: ['Point one'],
  important_terms: [],
  common_mistakes: [],
  exam_tips: { lecture_based: [], ai_suggestions: [] },
  difficulty: { level: 'Easy', reason: 'Introductory' },
  estimated_study_time: '20 minutes',
  prerequisites: [],
  learning_objectives: [],
  coverage: { status: 'Complete', estimated_completeness: 100 },
  confidence_notes: '',
};

const SUMMARY: GeneratedOutputSummary = {
  id: 12,
  course_id: 7,
  output_type: 'study_guide',
  user_id: 3,
  model_used: 'ollama:qwen3:8b',
  created_at: '2026-08-20T10:00:00Z',
  generation_settings: {
    version: 1,
    output_type: 'study_guide',
    summary_format: 'exam_tips',
    topic_focus: 'Graphs',
    summary_length: 'long',
    detail_level: 'detailed',
    summary_mode: 'exam_focused',
  },
  generation_context: null,
};

const DETAIL: GeneratedOutputDetail = {
  ...SUMMARY,
  content: STUDY_GUIDE_CONTENT,
};

function renderModal() {
  return render(
    <StudyHistoryModal courseId={7} courseName="Cell Biology" onClose={vi.fn()} />,
  );
}

describe('StudyHistoryModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('lists the stored outputs for the course', async () => {
    mockList.mockResolvedValue([SUMMARY]);

    renderModal();

    expect(await screen.findByText('Study guide')).toBeInTheDocument();
    expect(screen.getByText('ollama:qwen3:8b')).toBeInTheDocument();
    expect(screen.getByText('Graphs')).toBeInTheDocument();
    expect(screen.getByText('exam_focused')).toBeInTheDocument();
    expect(mockList).toHaveBeenCalledWith(7, expect.anything());
  });

  it('opens a stored guide when its entry is selected', async () => {
    mockList.mockResolvedValue([SUMMARY]);
    mockGet.mockResolvedValue(DETAIL);

    renderModal();
    await userEvent.click(await screen.findByText('Study guide'));

    expect(await screen.findByText('Stored Guide')).toBeInTheDocument();
    expect(screen.getByText('Stored summary')).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledWith(7, 12, expect.anything());
  });

  it('reports missing settings rather than inventing them', async () => {
    mockList.mockResolvedValue([{ ...SUMMARY, generation_settings: null }]);

    renderModal();

    expect(await screen.findByText('Settings not recorded')).toBeInTheDocument();
  });

  it('shows the raw document when a stored output cannot be rendered', async () => {
    mockList.mockResolvedValue([SUMMARY]);
    mockGet.mockResolvedValue({ ...DETAIL, content: 'not json at all' });

    renderModal();
    await userEvent.click(await screen.findByText('Study guide'));

    expect(await screen.findByText('not json at all')).toBeInTheDocument();
  });

  it('shows the raw document when stored study guide JSON no longer fits the schema', async () => {
    mockList.mockResolvedValue([SUMMARY]);
    mockGet.mockResolvedValue({ ...DETAIL, content: { title: 'Only a title' } });

    renderModal();
    await userEvent.click(await screen.findByText('Study guide'));

    expect(await screen.findByText(/Only a title/)).toBeInTheDocument();
  });

  it('explains an empty history instead of showing a blank panel', async () => {
    mockList.mockResolvedValue([]);

    renderModal();

    expect(await screen.findByRole('heading', { name: 'Nothing saved yet' })).toBeInTheDocument();
    expect(screen.getByText(/read them again without spending anything/)).toBeInTheDocument();
  });

  it('surfaces a failure to load the history', async () => {
    mockList.mockRejectedValue(new Error('boom'));

    renderModal();

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'The history could not be loaded.',
      ),
    );
  });
});
