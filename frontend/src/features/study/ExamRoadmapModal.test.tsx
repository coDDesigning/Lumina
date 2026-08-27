import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { examRoadmapAPI } from '@/api/examRoadmap';
import type { ExamRoadmapResult } from '@/api/types';
import { ExamRoadmapModal } from './ExamRoadmapModal';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

vi.mock('@/api/examRoadmap', () => ({
  examRoadmapAPI: { generate: vi.fn() },
}));

const mockGenerate = vi.mocked(examRoadmapAPI.generate);

const ROADMAP_RESULT: ExamRoadmapResult = {
  generated_output_id: 99,
  roadmap: {
    version: 1,
    output_type: 'exam_roadmap',
    course_id: 5,
    exam_date: '2026-09-18',
    generated_on: '2026-08-27',
    starts_on: '2026-08-27',
    days_until_exam: 22,
    scheduled_days: 23,
    lead_in_days: 0,
    horizon: 'standard',
    materials_available: true,
    attempts_considered: 0,
    roadmap_version: 1,
    adapted_from_output_id: null,
    notes: [],
    ranked_topics: [
      {
        topic: 'Graph Traversals',
        source: 'syllabus',
        syllabus_position: 0,
        importance: 1.0,
        mastery_percentage: null,
        questions_answered: 0,
        priority: 0.75,
      },
    ],
    days: [
      {
        day_index: 1,
        date: '2026-08-27',
        kind: 'study',
        is_exam_day: false,
        focus: 'First pass: Graph Traversals',
        topics: [
          {
            topic: 'Graph Traversals',
            goal: 'Trace BFS and DFS',
            pass_number: 1,
            source: 'syllabus',
            syllabus_position: 0,
            importance: 1.0,
            mastery_percentage: null,
            questions_answered: 0,
            priority: 0.75,
            material_status: 'resolved',
            materials: [],
            citations: [],
          },
        ],
      },
    ],
    deferred_topics: [],
  },
};

describe('ExamRoadmapModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders a notice to set an exam date when examDate is not provided', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    render(
      <ExamRoadmapModal
        courseId={5}
        courseName="Algorithms"
        examDate={null}
        onClose={onClose}
      />,
    );

    expect(screen.getByRole('heading', { level: 3, name: 'Exam Date Required' })).toBeInTheDocument();
    expect(screen.getByText(/Lumina needs to know when your exam takes place/)).toBeInTheDocument();

    const settingsBtn = screen.getByRole('button', { name: 'Go to Course Settings' });
    await user.click(settingsBtn);

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith('/courses/5/settings');
  });

  it('generates a roadmap when exam date is present and renders the result', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onGenerated = vi.fn();
    mockGenerate.mockResolvedValue(ROADMAP_RESULT);

    render(
      <ExamRoadmapModal
        courseId={5}
        courseName="Algorithms"
        examDate="2026-09-18"
        onClose={onClose}
        onGenerated={onGenerated}
      />,
    );

    expect(screen.getByLabelText('Daily pacing')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /Attach course materials/ })).toBeChecked();

    const generateBtn = screen.getByRole('button', { name: 'Generate Roadmap' });
    await user.click(generateBtn);

    expect(mockGenerate).toHaveBeenCalledWith(5, {
      max_topics_per_day: 3,
      include_materials: true,
    });

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 3, name: 'Study Schedule' })).toBeInTheDocument();
    });

    expect(onGenerated).toHaveBeenCalledWith(99);
    expect(screen.getByRole('button', { name: 'Done' })).toBeInTheDocument();
  });

  it('displays an error message if generation fails', async () => {
    const user = userEvent.setup();
    mockGenerate.mockRejectedValue(new Error('Exam date cannot be in the past'));

    render(
      <ExamRoadmapModal
        courseId={5}
        courseName="Algorithms"
        examDate="2026-08-01"
        onClose={vi.fn()}
      />,
    );

    const generateBtn = screen.getByRole('button', { name: 'Generate Roadmap' });
    await user.click(generateBtn);

    await waitFor(() => {
      expect(screen.getByText('Exam date cannot be in the past')).toBeInTheDocument();
    });
  });
});
