import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
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
    <MemoryRouter>
      <StudyHistoryModal courseId={7} courseName="Cell Biology" onClose={vi.fn()} />
    </MemoryRouter>,
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

  it('lets a stored guide be copied as markdown', async () => {
    mockList.mockResolvedValue([SUMMARY]);
    mockGet.mockResolvedValue(DETAIL);
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    renderModal();
    await userEvent.click(await screen.findByText('Study guide'));
    await userEvent.click(await screen.findByRole('button', { name: 'Copy' }));

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(writeText.mock.calls[0][0]).toContain('# Stored Guide');
    expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument();
  });

  it('offers no copy or download until a guide is open', async () => {
    mockList.mockResolvedValue([SUMMARY]);
    mockGet.mockResolvedValue(DETAIL);

    renderModal();
    await screen.findByText('Study guide');

    expect(screen.queryByRole('button', { name: 'Copy' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Download' })).not.toBeInTheDocument();
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

  it('lets a saved deck be flipped through, not just listed', async () => {
    mockList.mockResolvedValue([
      { ...SUMMARY, id: 20, output_type: 'flashcards', generation_settings: null },
    ]);
    mockGet.mockResolvedValue({
      ...SUMMARY,
      id: 20,
      output_type: 'flashcards',
      generation_settings: null,
      content: {
        deck_title: 'Graph Traversals',
        card_count: 2,
        flashcards: [
          { card_number: 1, front: 'What order does BFS settle in?', back: 'By distance.',
            difficulty: 'Easy' },
          { card_number: 2, front: 'What is a back edge?', back: 'A cycle.',
            difficulty: 'Medium' },
        ],
      },
    } as unknown as GeneratedOutputDetail);

    renderModal();
    await userEvent.click(await screen.findByRole('button', { name: /Flashcards/ }));

    expect(await screen.findByText('What order does BFS settle in?')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show the answer' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Show the answer' }));
    expect(screen.getByText('By distance.')).toBeInTheDocument();
  });

  it('renders stored quiz questions and retake action when a quiz output is opened', async () => {
    mockList.mockResolvedValue([
      {
        ...SUMMARY,
        id: 30,
        output_type: 'quiz',
        generation_settings: {
          version: 1,
          output_type: 'quiz',
          topic_focus: 'Cell Division',
          difficulty: 'medium',
          question_count: 1,
        },
      },
    ]);
    mockGet.mockResolvedValue({
      ...SUMMARY,
      id: 30,
      output_type: 'quiz',
      content: {
        quiz_id: 55,
        course_id: 7,
        title: 'Cell Division Quiz',
        created_at: '2026-08-20T10:00:00Z',
        questions: [
          {
            question_id: 101,
            question_number: 1,
            question_type: 'multiple_choice',
            difficulty: 'medium',
            topic: 'Mitosis',
            question: 'What phase comes after prophase?',
            options: ['Metaphase', 'Anaphase', 'Telophase', 'Interphase'],
            correct_option_index: 0,
            correct_answer: { type: 'multiple_choice', option_index: 0 },
            explanation: 'Metaphase follows prophase.',
          },
        ],
      },
    } as unknown as GeneratedOutputDetail);

    renderModal();
    await userEvent.click(await screen.findByRole('button', { name: /Practice quiz/ }));

    expect(await screen.findByText('Cell Division Quiz')).toBeInTheDocument();
    expect(screen.getByText('What phase comes after prophase?')).toBeInTheDocument();
    expect(screen.getByText('Metaphase')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Take this quiz' })).toHaveAttribute(
      'href',
      '/courses/7/practice/55',
    );
  });

  it('renders stored quiz when content is a stringified JSON string without falling through to raw view', async () => {
    const quizData = {
      quiz_id: 56,
      course_id: 7,
      title: 'Stringified Quiz Title',
      questions: [
        {
          question_id: 102,
          question_number: 1,
          question_type: 'multiple_choice',
          difficulty: 'easy',
          topic: 'Mitosis',
          question: 'Is mitosis nuclear division?',
          options: ['Yes', 'No'],
          correct_option_index: 0,
          explanation: 'Yes it is.',
        },
      ],
    };

    mockList.mockResolvedValue([
      {
        ...SUMMARY,
        id: 31,
        output_type: 'quiz',
      },
    ]);
    mockGet.mockResolvedValue({
      ...SUMMARY,
      id: 31,
      output_type: 'quiz',
      content: JSON.stringify(quizData),
    } as unknown as GeneratedOutputDetail);

    renderModal();
    await userEvent.click(await screen.findByRole('button', { name: /Practice quiz/ }));

    expect(await screen.findByText('Stringified Quiz Title')).toBeInTheDocument();
    expect(screen.getByText('Is mitosis nuclear division?')).toBeInTheDocument();
    expect(screen.queryByText(/This result was saved in a shape/)).not.toBeInTheDocument();
  });

  it('renders stored flashcards when content is a stringified JSON string', async () => {
    const flashcardData = {
      deck_title: 'Stringified Deck',
      card_count: 1,
      flashcards: [
        { card_number: 1, front: 'Stringified front', back: 'Stringified back', difficulty: 'Easy' },
      ],
    };

    mockList.mockResolvedValue([
      { ...SUMMARY, id: 21, output_type: 'flashcards' },
    ]);
    mockGet.mockResolvedValue({
      ...SUMMARY,
      id: 21,
      output_type: 'flashcards',
      content: JSON.stringify(flashcardData),
    } as unknown as GeneratedOutputDetail);

    renderModal();
    await userEvent.click(await screen.findByRole('button', { name: /Flashcards/ }));

    expect(await screen.findByText('Stringified front')).toBeInTheDocument();
    expect(screen.queryByText(/This result was saved in a shape/)).not.toBeInTheDocument();
  });

  it('renders stored flashcards when output_type is singular flashcard', async () => {
    mockList.mockResolvedValue([
      { ...SUMMARY, id: 22, output_type: 'flashcard' },
    ]);
    mockGet.mockResolvedValue({
      ...SUMMARY,
      id: 22,
      output_type: 'flashcard',
      content: {
        deck_title: 'Singular Type Deck',
        card_count: 1,
        flashcards: [
          { card_number: 1, front: 'Singular front', back: 'Singular back', difficulty: 'Medium' },
        ],
      },
    } as unknown as GeneratedOutputDetail);

    renderModal();
    await userEvent.click(await screen.findByRole('button', { name: /Flashcards/ }));

    expect(await screen.findByText('Singular front')).toBeInTheDocument();
  });

  it('renders a stored exam_roadmap using ExamRoadmapView instead of raw JSON', async () => {
    const roadmapContent = {
      version: 1,
      output_type: 'exam_roadmap',
      course_id: 7,
      exam_date: '2026-09-25',
      generated_on: '2026-08-27',
      starts_on: '2026-08-27',
      days_until_exam: 29,
      scheduled_days: 30,
      lead_in_days: 0,
      horizon: 'standard',
      materials_available: true,
      attempts_considered: 1,
      roadmap_version: 1,
      adapted_from_output_id: null,
      notes: [],
      ranked_topics: [],
      days: [
        {
          day_index: 1,
          date: '2026-08-27',
          kind: 'study',
          is_exam_day: false,
          focus: 'First pass: Memory Systems',
          topics: [
            {
              topic: 'Memory Systems',
              goal: 'Understand working memory',
              pass_number: 1,
              source: 'syllabus',
              syllabus_position: 0,
              importance: 1.0,
              mastery_percentage: null,
              questions_answered: 0,
              priority: 0.8,
              material_status: 'resolved',
              materials: [],
              citations: [],
            },
          ],
        },
      ],
      deferred_topics: [],
    };

    mockList.mockResolvedValue([
      { ...SUMMARY, id: 30, output_type: 'exam_roadmap', generation_settings: null },
    ]);
    mockGet.mockResolvedValue({
      ...SUMMARY,
      id: 30,
      output_type: 'exam_roadmap',
      content: roadmapContent,
    } as unknown as GeneratedOutputDetail);

    renderModal();
    await userEvent.click(await screen.findByRole('button', { name: /Exam roadmap/ }));

    expect(await screen.findByText('Daily Schedule')).toBeInTheDocument();
    expect(screen.getByText('First pass: Memory Systems')).toBeInTheDocument();
    expect(screen.getByText('Understand working memory')).toBeInTheDocument();
    expect(screen.queryByText(/This result was saved in a shape/)).not.toBeInTheDocument();
  });
});

