import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { examModeAPI } from '@/api/examMode';
import { APIError } from '@/api/client';
import type { ExamSourceInventory } from '@/api/types';
import { queryCache } from '@/lib/query/cache';
import { useExamMode, useReadiness } from './useExamMode';

vi.mock('@/api/examMode', () => ({
  examModeAPI: {
    listSources: vi.fn(),
    listPlans: vi.fn(),
    getAnalysis: vi.fn(),
    listEntitlements: vi.fn(),
  },
}));

const listSources = vi.mocked(examModeAPI.listSources);
const listPlans = vi.mocked(examModeAPI.listPlans);
const getAnalysis = vi.mocked(examModeAPI.getAnalysis);
const listEntitlements = vi.mocked(examModeAPI.listEntitlements);

function inventory(overrides: Partial<ExamSourceInventory> = {}): ExamSourceInventory {
  return {
    syllabus_present: true,
    syllabus_characters: 1200,
    course_topics: ['Photosynthesis'],
    documents: [
      {
        id: 'doc-1',
        label: 'Lecture 1.pdf',
        material_kind: 'lecture_notes',
        status: 'ready',
        is_past_exam: false,
        is_syllabus: false,
      },
    ],
    ready_document_count: 1,
    past_exam_document_count: 0,
    chunks_available: 12,
    ...overrides,
  };
}

const PLANS = { plans: [], current_plan_output_id: null };

describe('useExamMode', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryCache.clear();
    listSources.mockResolvedValue(inventory());
    listPlans.mockResolvedValue(PLANS);
    getAnalysis.mockRejectedValue(new APIError(404, 'Not Found'));
    listEntitlements.mockResolvedValue({ unlocked_topic_keys: [] });
  });

  it('reports loading until the two course-level reads settle', async () => {
    const { result } = renderHook(() => useExamMode(1));

    expect(result.current.isLoading).toBe(true);

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.error).toBeNull();
  });

  it('treats a course that was never analysed as empty rather than broken', async () => {
    const { result } = renderHook(() => useExamMode(1));

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(result.current.hasAnalysis).toBe(false);
    expect(result.current.analysis).toBeUndefined();
    expect(result.current.error).toBeNull();
  });

  it('surfaces an error when the source inventory cannot be read', async () => {
    listSources.mockRejectedValue(new APIError(500, 'boom'));

    const { result } = renderHook(() => useExamMode(1));

    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error).toBeTruthy();
  });

  it('surfaces an error when the saved plans cannot be read', async () => {
    listPlans.mockRejectedValue(new APIError(500, 'boom'));

    const { result } = renderHook(() => useExamMode(1));

    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error).toBeTruthy();
  });

  it('exposes the unlocked topics an owner has already paid for', async () => {
    listEntitlements.mockResolvedValue({ unlocked_topic_keys: ['photosynthesis'] });

    const { result } = renderHook(() => useExamMode(1));

    await waitFor(() => expect(result.current.unlockedTopicKeys.size).toBe(1));
    expect(result.current.unlockedTopicKeys.has('photosynthesis')).toBe(true);
  });

  it('never asks for entitlements on behalf of a read-only viewer', async () => {
    const { result } = renderHook(() => useExamMode(1, { readOnly: true }));

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(listEntitlements).not.toHaveBeenCalled();
    expect(result.current.unlockedTopicKeys.size).toBe(0);
  });

  it('refetches every read when reloaded', async () => {
    const { result } = renderHook(() => useExamMode(1));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    result.current.reload();

    await waitFor(() => expect(listSources.mock.calls.length).toBeGreaterThan(1));
    expect(listPlans.mock.calls.length).toBeGreaterThan(1);
  });
});

describe('useReadiness', () => {
  const today = new Date('2026-01-01T00:00:00Z');

  it('reports nothing until the inventory has arrived', () => {
    const { result } = renderHook(() =>
      useReadiness({ inventory: undefined, examDate: '2026-06-01', planCount: 0, today }),
    );

    expect(result.current).toBeNull();
  });

  it('blocks a course with no ready sources', () => {
    const { result } = renderHook(() =>
      useReadiness({
        inventory: inventory({ documents: [], ready_document_count: 0 }),
        examDate: '2026-06-01',
        planCount: 0,
        today,
      }),
    );

    expect(result.current?.blockers.some((b) => b.kind === 'no_sources')).toBe(true);
  });

  it('separates an indexing gap from a missing upload', () => {
    const { result } = renderHook(() =>
      useReadiness({
        inventory: inventory({ chunks_available: 0 }),
        examDate: '2026-06-01',
        planCount: 0,
        today,
      }),
    );

    const kinds = result.current?.blockers.map((b) => b.kind) ?? [];
    expect(kinds).toContain('material_not_indexed');
    expect(kinds).not.toContain('no_sources');
  });

  it('warns rather than blocks when a course has no syllabus', () => {
    const { result } = renderHook(() =>
      useReadiness({
        inventory: inventory({ syllabus_present: false }),
        examDate: '2026-06-01',
        planCount: 0,
        today,
      }),
    );

    expect(result.current?.warnings.some((w) => w.kind === 'no_syllabus')).toBe(true);
    expect(result.current?.blockers).toHaveLength(0);
  });

  it('treats an empty exam date as absent', () => {
    const { result } = renderHook(() =>
      useReadiness({ inventory: inventory(), examDate: '', planCount: 0, today }),
    );

    expect(result.current).not.toBeNull();
  });
});
