import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MalformedResponseError } from './client';
import { quizAPI } from './quiz';
import type {
  QuizAttemptResponse,
  QuizGenerationResult,
  QuizHistoryItem,
  QuizSessionView,
  QuizSummary,
  QuizView,
} from './types';

const QUIZ_VIEW: QuizView = {
  quiz_id: 1,
  course_id: 10,
  title: 'Sample Quiz',
  created_at: '2026-08-23T12:00:00Z',
  user_id: 1,
  model_used: 'ollama:qwen3:8b',
  generation_settings: null,
  generation_context: null,
  quiz_purpose: null,
  exam_plan_output_id: null,
  exam_topic_key: null,
  timed: false,
  time_limit_seconds: null,
  answers_hidden: false,
  questions: [
    {
      question_id: 101,
      question_number: 1,
      question_type: 'multiple_choice',
      difficulty: 'medium',
      topic: 'Algebra',
      question: 'What is x in 2x=4?',
      options: ['1', '2', '3', '4'],
      correct_option_index: 1,
      correct_answer: { type: 'multiple_choice', option_index: 1 },
      explanation: 'Divide both sides by 2.',
    },
  ],
};

const QUIZ_SUMMARY: QuizSummary = {
  quiz_id: 1,
  course_id: 10,
  title: 'Sample Quiz',
  question_count: 1,
  attempts_count: 1,
  best_score: 1.0,
  last_score: 1.0,
  created_at: '2026-08-23T12:00:00Z',
  user_id: 1,
  model_used: 'ollama:qwen3:8b',
  generation_settings: null,
  generation_context: null,
  quiz_purpose: null,
  exam_plan_output_id: null,
  exam_topic_key: null,
  timed: false,
  time_limit_seconds: null,
};

const QUIZ_GEN_RESULT: QuizGenerationResult = {
  quiz: QUIZ_VIEW,
  generated_output_id: 50,
  context_truncated: false,
  retrieval_narrowed: true,
  lowest_similarity: 0.5,
  highest_similarity: 0.9,
  chunks_used: 2,
  chunks_available: 4,
};

const QUIZ_ATTEMPT_RESPONSE: QuizAttemptResponse = {
  attempt_id: 201,
  quiz_id: 1,
  score: 1.0,
  correct_count: 1,
  graded_count: 1,
  total_questions: 1,
  time_spent_seconds: 30,
  created_at: '2026-08-23T12:05:00Z',
  quiz_purpose: null,
  timed: false,
  expired: false,
  answers: [
    {
      question_id: 101,
      question_type: 'multiple_choice',
      selected_option_index: 1,
      text_response: null,
      correct_option_index: 1,
      correct_answer: { type: 'multiple_choice', option_index: 1 },
      is_correct: true,
      score: 1.0,
      feedback: 'Correct!',
      time_spent_seconds: 30,
      topic: 'Algebra',
    },
  ],
};

const QUIZ_HISTORY_ITEM: QuizHistoryItem = {
  attempt_id: 201,
  quiz_id: 1,
  score: 1.0,
  correct_count: 1,
  total_questions: 1,
  time_spent_seconds: 30,
  created_at: '2026-08-23T12:05:00Z',
  quiz_purpose: null,
  timed: false,
  expired: false,
};

const QUIZ_SESSION: QuizSessionView = {
  session_id: 55,
  quiz_id: 1,
  status: 'active',
  started_at: '2026-08-23T12:00:00Z',
  expires_at: '2026-08-23T13:00:00Z',
  time_limit_seconds: 3600,
  seconds_remaining: 3400,
  elapsed_seconds: 200,
  answered_count: 1,
  answers: [{ question_id: 101, selected_option_index: 1, text_response: null }],
  attempt_id: null,
};

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: 'OK',
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as Response;
}

describe('quizAPI', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token');
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  describe('generate', () => {
    it('posts request and unwraps quiz generation result', async () => {
      const fetchMock = vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: QUIZ_GEN_RESULT }),
      );
      vi.stubGlobal('fetch', fetchMock);

      const result = await quizAPI.generate(10, { question_count: 1 });
      expect(result).toEqual(QUIZ_GEN_RESULT);
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/courses/10/quiz',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ question_count: 1 }),
        }),
      );
    });
  });

  describe('list', () => {
    it('fetches quizzes for course and unwraps data', async () => {
      const fetchMock = vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: [QUIZ_SUMMARY] }),
      );
      vi.stubGlobal('fetch', fetchMock);

      const result = await quizAPI.list(10);
      expect(result).toEqual([QUIZ_SUMMARY]);
      expect(fetchMock).toHaveBeenCalledWith('/api/courses/10/quizzes', expect.anything());
    });
  });

  describe('get', () => {
    it('fetches quiz by id and unwraps view', async () => {
      const fetchMock = vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: QUIZ_VIEW }),
      );
      vi.stubGlobal('fetch', fetchMock);

      const result = await quizAPI.get(10, 1);
      expect(result).toEqual(QUIZ_VIEW);
      expect(fetchMock).toHaveBeenCalledWith('/api/courses/10/quizzes/1', expect.anything());
    });
  });

  describe('submitAttempt', () => {
    it('posts attempt answers and unwraps attempt response', async () => {
      const fetchMock = vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: QUIZ_ATTEMPT_RESPONSE }),
      );
      vi.stubGlobal('fetch', fetchMock);

      const result = await quizAPI.submitAttempt(10, 1, {
        answers: [{ question_id: 101, selected_option_index: 1 }],
        time_spent_seconds: 30,
      });
      expect(result).toEqual(QUIZ_ATTEMPT_RESPONSE);
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/courses/10/quizzes/1/attempts',
        expect.objectContaining({
          method: 'POST',
        }),
      );
    });
  });

  describe('listAttempts', () => {
    it('fetches attempts for quiz and unwraps data', async () => {
      const fetchMock = vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: [QUIZ_HISTORY_ITEM] }),
      );
      vi.stubGlobal('fetch', fetchMock);

      const result = await quizAPI.listAttempts(10, 1);
      expect(result).toEqual([QUIZ_HISTORY_ITEM]);
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/courses/10/quizzes/1/attempts',
        expect.anything(),
      );
    });

    it('throws MalformedResponseError when data is missing', async () => {
      const fetchMock = vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: null }),
      );
      vi.stubGlobal('fetch', fetchMock);

      await expect(quizAPI.listAttempts(10, 1)).rejects.toBeInstanceOf(
        MalformedResponseError,
      );
    });
  });

  describe('getAttempt', () => {
    it('fetches attempt detail and unwraps data', async () => {
      const fetchMock = vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: QUIZ_ATTEMPT_RESPONSE }),
      );
      vi.stubGlobal('fetch', fetchMock);

      const result = await quizAPI.getAttempt(10, 1, 201);
      expect(result).toEqual(QUIZ_ATTEMPT_RESPONSE);
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/courses/10/quizzes/1/attempts/201',
        expect.anything(),
      );
    });

    it('throws MalformedResponseError when data is missing', async () => {
      const fetchMock = vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: null }),
      );
      vi.stubGlobal('fetch', fetchMock);

      await expect(quizAPI.getAttempt(10, 1, 201)).rejects.toBeInstanceOf(
        MalformedResponseError,
      );
    });
  });

  describe('timed sittings', () => {
    it('opens a sitting without sending a clock of its own', async () => {
      // started_at and expires_at belong to the server. A body here would be a
      // deadline the candidate proposed.
      const fetchMock = vi.fn<typeof fetch>(async () =>
        jsonResponse({
          success: true,
          message: 'ok',
          data: { session: QUIZ_SESSION, quiz: QUIZ_VIEW },
        }),
      );
      vi.stubGlobal('fetch', fetchMock);

      const result = await quizAPI.startSession(10, 1);

      expect(result).toEqual({ session: QUIZ_SESSION, quiz: QUIZ_VIEW });
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/courses/10/quizzes/1/sessions',
        expect.objectContaining({ method: 'POST', body: undefined }),
      );
    });

    it('reads a sitting back with the drafts already saved', async () => {
      const fetchMock = vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: QUIZ_SESSION }),
      );
      vi.stubGlobal('fetch', fetchMock);

      const result = await quizAPI.getSession(10, 1, 55);

      expect(result.answers).toEqual(QUIZ_SESSION.answers);
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/courses/10/quizzes/1/sessions/55',
        expect.anything(),
      );
    });

    it('saves one answer against the question it belongs to', async () => {
      const fetchMock = vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: QUIZ_SESSION }),
      );
      vi.stubGlobal('fetch', fetchMock);

      await quizAPI.saveSessionAnswer(10, 1, 55, 101, {
        question_id: 101,
        selected_option_index: 1,
      });

      expect(fetchMock).toHaveBeenCalledWith(
        '/api/courses/10/quizzes/1/sessions/55/answers/101',
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({ question_id: 101, selected_option_index: 1 }),
        }),
      );
    });

    it('finalises a sitting into an ordinary attempt', async () => {
      const fetchMock = vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: QUIZ_ATTEMPT_RESPONSE }),
      );
      vi.stubGlobal('fetch', fetchMock);

      const result = await quizAPI.submitSession(10, 1, 55);

      expect(result).toEqual(QUIZ_ATTEMPT_RESPONSE);
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/courses/10/quizzes/1/sessions/55/submit',
        expect.objectContaining({ method: 'POST' }),
      );
    });

    it('forwards an abort signal to every session read', async () => {
      const fetchMock = vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: QUIZ_SESSION }),
      );
      vi.stubGlobal('fetch', fetchMock);
      const controller = new AbortController();

      await quizAPI.getSession(10, 1, 55, { signal: controller.signal });

      expect(fetchMock).toHaveBeenCalledWith(
        '/api/courses/10/quizzes/1/sessions/55',
        expect.objectContaining({ signal: controller.signal }),
      );
    });

    it('refuses a session response carrying no data', async () => {
      const fetchMock = vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: null }),
      );
      vi.stubGlobal('fetch', fetchMock);

      await expect(quizAPI.getSession(10, 1, 55)).rejects.toBeInstanceOf(
        MalformedResponseError,
      );
    });

    it('keeps the error code a refused sitting was refused with', async () => {
      // 409 covers four distinct session refusals, so the header is the
      // contract and the status alone would merge them.
      const fetchMock = vi.fn<typeof fetch>(async () => ({
        ...jsonResponse({ detail: 'This quiz is sat against a clock.' }, 409),
        headers: new Headers({ 'X-Error-Code': 'timed_session_required' }),
      }));
      vi.stubGlobal('fetch', fetchMock);

      await expect(
        quizAPI.submitAttempt(10, 1, { answers: [{ question_id: 101 }] }),
      ).rejects.toMatchObject({ status: 409, code: 'timed_session_required' });
    });
  });
});
