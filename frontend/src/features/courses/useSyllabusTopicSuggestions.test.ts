import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { coursesAPI } from '@/api/courses';
import type { SuggestedTopic } from '@/api/types';
import { useSyllabusTopicSuggestions } from './useSyllabusTopicSuggestions';

vi.mock('@/api/courses', () => ({
  coursesAPI: {
    suggestSyllabusTopics: vi.fn(),
  },
}));

const suggestSyllabusTopics = vi.mocked(coursesAPI.suggestSyllabusTopics);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const TOPICS_A: SuggestedTopic[] = [{ name: 'Graph Traversal', weight_percent: 20 }];
const TOPICS_B: SuggestedTopic[] = [{ name: 'Deadlocks', weight_percent: null }];

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('useSyllabusTopicSuggestions', () => {
  it('shows nothing before suggest is ever called', () => {
    const { result } = renderHook(({ text }) => useSyllabusTopicSuggestions(text), {
      initialProps: { text: 'Syllabus A' },
    });

    expect(result.current.suggestions).toEqual([]);
    expect(result.current.isPending).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('discards results for A once the text is edited to B', async () => {
    suggestSyllabusTopics.mockResolvedValueOnce(TOPICS_A);
    const { result, rerender } = renderHook(({ text }) => useSyllabusTopicSuggestions(text), {
      initialProps: { text: 'Syllabus A' },
    });

    act(() => result.current.suggest('Syllabus A'));
    await flush();
    expect(result.current.suggestions).toEqual(TOPICS_A);

    rerender({ text: 'Syllabus B' });

    expect(result.current.suggestions).toEqual([]);
    expect(result.current.isPending).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('never shows topics from a request for A that resolves after the edit to B', async () => {
    const pendingForA = deferred<SuggestedTopic[]>();
    suggestSyllabusTopics.mockReturnValueOnce(pendingForA.promise);
    const { result, rerender } = renderHook(({ text }) => useSyllabusTopicSuggestions(text), {
      initialProps: { text: 'Syllabus A' },
    });

    act(() => result.current.suggest('Syllabus A'));
    expect(result.current.isPending).toBe(true);

    rerender({ text: 'Syllabus B' });
    expect(result.current.isPending).toBe(false);

    pendingForA.resolve(TOPICS_A);
    await flush();

    expect(result.current.suggestions).toEqual([]);
    expect(result.current.isPending).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('shows the error for A only while the text still reads A', async () => {
    suggestSyllabusTopics.mockRejectedValueOnce(new Error('boom'));
    const { result, rerender } = renderHook(({ text }) => useSyllabusTopicSuggestions(text), {
      initialProps: { text: 'Syllabus A' },
    });

    act(() => result.current.suggest('Syllabus A'));
    await flush();
    expect(result.current.error).not.toBeNull();

    rerender({ text: 'Syllabus B' });
    expect(result.current.error).toBeNull();
  });

  it('shows fresh results once re-suggested for the text now in the box', async () => {
    const pendingForA = deferred<SuggestedTopic[]>();
    suggestSyllabusTopics.mockReturnValueOnce(pendingForA.promise);
    const { result, rerender } = renderHook(({ text }) => useSyllabusTopicSuggestions(text), {
      initialProps: { text: 'Syllabus A' },
    });

    act(() => result.current.suggest('Syllabus A'));
    rerender({ text: 'Syllabus B' });

    suggestSyllabusTopics.mockResolvedValueOnce(TOPICS_B);
    act(() => result.current.suggest('Syllabus B'));
    await flush();

    pendingForA.resolve(TOPICS_A);
    await flush();

    expect(result.current.suggestions).toEqual(TOPICS_B);
    expect(suggestSyllabusTopics).toHaveBeenCalledTimes(2);
  });

  it('brings a completed result back once the box reads the exact text it was produced for again, without a second request', async () => {
    suggestSyllabusTopics.mockResolvedValueOnce(TOPICS_A);
    const { result, rerender } = renderHook(({ text }) => useSyllabusTopicSuggestions(text), {
      initialProps: { text: 'Syllabus A' },
    });

    act(() => result.current.suggest('Syllabus A'));
    await flush();
    expect(result.current.suggestions).toEqual(TOPICS_A);

    rerender({ text: '' });
    expect(result.current.suggestions).toEqual([]);

    rerender({ text: 'Syllabus A' });

    expect(result.current.suggestions).toEqual(TOPICS_A);
    expect(suggestSyllabusTopics).toHaveBeenCalledTimes(1);
  });

  it('clear() hides the result even if the box still reads the text it was produced for', async () => {
    suggestSyllabusTopics.mockResolvedValueOnce(TOPICS_A);
    const { result, rerender } = renderHook(({ text }) => useSyllabusTopicSuggestions(text), {
      initialProps: { text: 'Syllabus A' },
    });

    act(() => result.current.suggest('Syllabus A'));
    await flush();
    expect(result.current.suggestions).toEqual(TOPICS_A);

    act(() => result.current.clear());
    rerender({ text: 'Syllabus A' });

    expect(result.current.suggestions).toEqual([]);
    expect(result.current.isPending).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('ignores a response that arrives after unmount', async () => {
    const pending = deferred<SuggestedTopic[]>();
    suggestSyllabusTopics.mockReturnValueOnce(pending.promise);
    const { result, unmount } = renderHook(({ text }) => useSyllabusTopicSuggestions(text), {
      initialProps: { text: 'Syllabus A' },
    });

    act(() => result.current.suggest('Syllabus A'));
    unmount();

    pending.resolve(TOPICS_A);
    await flush();
  });
});
