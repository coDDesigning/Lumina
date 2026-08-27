import { describe, expect, it, vi } from 'vitest';
import { generateReverseQuiz, getReverseQuizzes } from './reverseQuiz';
import { apiClient } from './client';

vi.mock('./client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./client')>();
  return {
    ...actual,
    apiClient: {
      post: vi.fn(),
      get: vi.fn(),
    },
  };
});

describe('reverseQuiz API', () => {
  it('generateReverseQuiz calls client.post with correct arguments', async () => {
    const mockData = { success: true, data: { id: 1 } };
    vi.mocked(apiClient.post).mockResolvedValueOnce(mockData);

    const result = await generateReverseQuiz(1, {
      topic: 'test',
      explanation: 'test explanation',
    });

    expect(apiClient.post).toHaveBeenCalledWith(
      '/courses/1/reverse-quiz',
      { topic: 'test', explanation: 'test explanation' },
      { signal: undefined }
    );
    expect(result).toEqual(mockData.data);
  });

  it('getReverseQuizzes calls client.get with correct arguments', async () => {
    const mockData = { success: true, data: [] };
    vi.mocked(apiClient.get).mockResolvedValueOnce(mockData);

    const result = await getReverseQuizzes(1);

    expect(apiClient.get).toHaveBeenCalledWith('/courses/1/reverse-quizzes', {
      signal: undefined,
    });
    expect(result).toEqual(mockData.data);
  });
});
