import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { generatedOutputsAPI } from '@/api/generatedOutputs';
import type { GeneratedOutputDetail } from '@/api/types';
import { StudyGuideViewerModal } from './StudyGuideViewerModal';

vi.mock('@/api/generatedOutputs', () => ({
  generatedOutputsAPI: { list: vi.fn(), get: vi.fn() },
}));

const mockGet = vi.mocked(generatedOutputsAPI.get);

const DETAIL: GeneratedOutputDetail = {
  id: 12,
  course_id: 7,
  output_type: 'study_guide',
  user_id: 3,
  model_used: 'ollama:qwen3:8b',
  created_at: '2026-08-20T10:00:00Z',
  generation_settings: null,
  generation_context: null,
  content: {
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
  },
};

function renderViewer() {
  return render(
    <StudyGuideViewerModal
      courseId={7}
      courseName="Cell Biology"
      outputId={12}
      onClose={vi.fn()}
    />,
  );
}

describe('StudyGuideViewerModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('opens the guide on its own rather than inside the history panel', async () => {
    mockGet.mockResolvedValue(DETAIL);

    renderViewer();

    expect(await screen.findByText('Stored Guide')).toBeInTheDocument();
    expect(screen.getByText('Stored summary')).toBeInTheDocument();
    expect(screen.queryByText('Made for you')).not.toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledWith(7, 12, expect.anything());
  });

  it('carries the copy and download a reader takes the guide away with', async () => {
    mockGet.mockResolvedValue(DETAIL);
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    renderViewer();
    await screen.findByText('Stored Guide');
    await userEvent.click(screen.getByRole('button', { name: 'Copy' }));

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(writeText.mock.calls[0][0]).toContain('# Stored Guide');
    expect(screen.getByRole('button', { name: 'Download' })).toBeInTheDocument();
  });

  it('reports a failure to open instead of showing an empty dialog', async () => {
    mockGet.mockRejectedValue(new Error('boom'));

    renderViewer();

    expect(await screen.findByText('This study guide could not be opened.')).toBeInTheDocument();
  });
});
