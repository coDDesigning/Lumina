import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { generatedOutputsAPI } from '@/api/generatedOutputs';
import type { GeneratedOutputDetail, StudyGuideResponse } from '@/api/types';
import type { Workspace } from '@/data/workspaces';
import GuidePage from './GuidePage';

vi.mock('@/api/generatedOutputs', () => ({
  generatedOutputsAPI: { get: vi.fn(), list: vi.fn() },
}));

const mockGet = vi.mocked(generatedOutputsAPI.get);

const WORKSPACE: Workspace = {
  id: '10',
  name: 'Algorithms',
  subjectArea: 'Computer Science',
  educationLevel: 'undergraduate',
  semester: 'Fall 2026',
  examDate: '2026-12-01',
  topics: ['Sorting'],
  syllabus: 'Sorting and searching.',
  progress: null,
  updatedAt: 'Updated today',
  accent: 'blue',
};

const GUIDE: StudyGuideResponse = {
  title: 'Sorting Algorithms',
  summary: 'Sorting puts a sequence in order.',
  key_points: ['Merge sort is stable'],
  important_terms: [{ term: 'Stability', definition: 'Equal keys keep their order.' }],
  common_mistakes: [{ mistake: 'Assuming quicksort is stable', correction: 'It is not.' }],
  exam_tips: { lecture_based: ['Know the recurrences'], ai_suggestions: [] },
  difficulty: { level: 'Medium', reason: 'Mixed material' },
  estimated_study_time: '45 minutes',
  prerequisites: [],
  learning_objectives: [],
  coverage: { status: 'Partial', estimated_completeness: 40 },
  confidence_notes: '',
};

function output(overrides: Partial<GeneratedOutputDetail> = {}): GeneratedOutputDetail {
  return {
    id: 5,
    course_id: 10,
    output_type: 'study_guide',
    created_at: '2026-08-23T10:00:00Z',
    user_id: 1,
    model_used: 'ollama:qwen3:8b',
    generation_settings: null,
    generation_context: null,
    content: GUIDE as unknown as Record<string, unknown>,
    ...overrides,
  };
}

function renderPage(address = '5') {
  const person = userEvent.setup();
  render(
    <MemoryRouter initialEntries={[`/courses/10/guides/${address}`]}>
      <Routes>
        <Route
          path="/courses/:id/guides/:outputId"
          element={<GuidePage workspace={WORKSPACE} />}
        />
        <Route path="/courses/:id" element={<p>The course page</p>} />
      </Routes>
    </MemoryRouter>,
  );
  return person;
}

beforeEach(() => {
  mockGet.mockResolvedValue(output());
});

describe('opening a stored study guide', () => {
  it('reads the guide the address names', async () => {
    renderPage();

    expect(await screen.findByText('Sorting puts a sequence in order.')).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledWith(10, 5, expect.anything());
  });

  it('refuses an address that is not a guide id, without asking the server', async () => {
    renderPage('not-a-number');

    expect(await screen.findByText('That is not a study guide address.')).toBeInTheDocument();
    expect(mockGet).not.toHaveBeenCalled();
  });

  it('says the guide could not be opened rather than showing an empty page', async () => {
    mockGet.mockRejectedValue(new APIError(500, { detail: 'boom' }));
    renderPage();

    expect(await screen.findByText('This guide is not here')).toBeInTheDocument();
  });

  it('offers a way back to the course when the guide cannot be read', async () => {
    mockGet.mockRejectedValue(new APIError(404, { detail: 'Not found' }));
    const person = renderPage();

    await person.click(await screen.findByRole('button', { name: /back to the course/i }));

    expect(await screen.findByText('The course page')).toBeInTheDocument();
  });

  it('names a flashcard deck for what it is instead of failing to render it', async () => {
    mockGet.mockResolvedValue(
      output({ output_type: 'flashcards', content: { deck_title: 'Cards', flashcards: [] } }),
    );
    renderPage();

    expect(
      await screen.findByText('This is a flashcard deck, not a study guide'),
    ).toBeInTheDocument();
  });
});

describe('what the stored guide reports about its material', () => {
  it('says which part of the course the guide was built from', async () => {
    mockGet.mockResolvedValue(
      output({
        generation_context: {
          version: 1,
          chunks_used: 4,
          chunks_available: 9,
          truncated: false,
        },
      }),
    );
    renderPage();

    await screen.findByText('Sorting puts a sequence in order.');
    expect(
      screen.getByText('Built from the passages closest to your topic'),
    ).toBeInTheDocument();
    expect(screen.getByText(/Of 9 passages in this course/)).toBeInTheDocument();
  });

  it('separates a dropped passage from a narrowed search', async () => {
    mockGet.mockResolvedValue(
      output({
        generation_context: {
          version: 1,
          chunks_used: 9,
          chunks_available: 9,
          truncated: true,
        },
      }),
    );
    renderPage();

    await screen.findByText('Sorting puts a sequence in order.');
    expect(screen.getByText('Some selected passages did not fit')).toBeInTheDocument();
    expect(screen.queryByText('Built from the passages closest to your topic')).toBeNull();
  });

  it('says nothing about material when the row stored no context at all', async () => {
    renderPage();

    await screen.findByText('Sorting puts a sequence in order.');
    expect(screen.queryByText(/passages/)).toBeNull();
  });

  it('claims no narrowing when the whole course was read', async () => {
    mockGet.mockResolvedValue(
      output({
        generation_context: {
          version: 1,
          chunks_used: 9,
          chunks_available: 9,
          truncated: false,
        },
      }),
    );
    renderPage();

    await screen.findByText('Sorting puts a sequence in order.');
    expect(screen.queryByText(/passages/)).toBeNull();
  });
});

describe('sources on a reopened guide', () => {
  const CITATION = {
    key: 'S1',
    document_id: '11111111-1111-1111-1111-111111111111',
    document_label: 'Lecture 4',
    page_start: 12,
    page_end: 12,
  };

  it('names the sources a stored guide was generated with', async () => {
    mockGet.mockResolvedValue(
      output({
        content: {
          ...GUIDE,
          summary: { text: 'Sorting puts a sequence in order.', citations: [CITATION] },
          key_points: [{ text: 'Merge sort is stable', citations: [CITATION] }],
        } as unknown as Record<string, unknown>,
      }),
    );

    renderPage();

    expect(await screen.findByText('Sorting Algorithms')).toBeInTheDocument();
    expect(screen.getAllByText('Lecture 4 · p. 12').length).toBeGreaterThan(0);
  });

  it('still opens a guide stored before citations existed', async () => {
    mockGet.mockResolvedValue(output());

    renderPage();

    expect(await screen.findByText('Sorting Algorithms')).toBeInTheDocument();
    expect(screen.getByText('Merge sort is stable')).toBeInTheDocument();
    expect(screen.queryByText('Sources:')).not.toBeInTheDocument();
  });
});
