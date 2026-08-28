import { useCallback, useEffect, useRef, useState } from 'react';
import { describeGenerationError } from '@/api/errors';
import type { GenerationFailure } from '@/api/errors';
import { quizAPI } from '@/api/quiz';
import type { QuizAnswerSubmission, QuizQuestionView, QuizSessionView } from '@/api/types';
import { isOptionBased } from '@/api/types';
import type { AnswerDraft } from './answerDraft';

/** How often the visible clock is checked against the server's own deadline. */
const RECONCILE_MS = 30_000;

/** Long enough that typing is not a request per keystroke, short enough to survive a crash. */
export const WRITE_DEBOUNCE_MS = 800;

function draftsFrom(answers: QuizAnswerSubmission[]): Record<number, AnswerDraft> {
  const restored: Record<number, AnswerDraft> = {};
  for (const answer of answers) {
    restored[answer.question_id] = {
      optionIndex: answer.selected_option_index ?? null,
      text: answer.text_response ?? '',
    };
  }
  return restored;
}

function secondsUntil(expiresAt: string): number {
  const deadline = Date.parse(expiresAt);
  if (Number.isNaN(deadline)) return 0;
  return Math.max(0, Math.round((deadline - Date.now()) / 1000));
}

export interface TimedSession {
  session: QuizSessionView | null;
  answers: Record<number, AnswerDraft>;
  secondsRemaining: number;
  expired: boolean;
  submitted: boolean;
  saveError: GenerationFailure | null;
  setAnswer: (question: QuizQuestionView, draft: AnswerDraft) => void;
  /** Write anything still waiting on the debounce, before handing in. */
  flush: () => Promise<void>;
}

/**
 * One sitting of a timed paper, with the clock and the answers on the server.
 *
 * A countdown the browser keeps is a number the candidate can edit, so the
 * deadline is only ever derived from `expires_at` and re-read from the server
 * periodically rather than accumulated locally -- a throttled background tab
 * would otherwise drift the clock in the candidate's favour.
 *
 * Answers are written as they are given. That is what makes the deadline cost
 * the right to keep answering and nothing else: work saved before it is never
 * discarded, and a submission that arrives after it finalises exactly that work.
 */
export function useTimedSession(
  courseId: number,
  quizId: number,
  sessionId: number,
  initial: QuizSessionView | null,
): TimedSession {
  const [session, setSession] = useState<QuizSessionView | null>(initial);
  const [answers, setAnswers] = useState<Record<number, AnswerDraft>>({});
  const [secondsRemaining, setSecondsRemaining] = useState(0);
  const [saveError, setSaveError] = useState<GenerationFailure | null>(null);

  const restored = useRef(false);
  /** Question id -> the write waiting on its debounce, payload included. */
  const pending = useRef(
    new Map<number, { timer: ReturnType<typeof setTimeout>; question: QuizQuestionView; draft: AnswerDraft }>(),
  );
  const inFlight = useRef<Promise<unknown>[]>([]);

  // Restore once. A later read must not overwrite what is being typed now.
  useEffect(() => {
    if (restored.current || !initial) return;
    restored.current = true;
    setSession(initial);
    setAnswers(draftsFrom(initial.answers));
    setSecondsRemaining(secondsUntil(initial.expires_at));
  }, [initial]);

  const expiresAt = session?.expires_at;
  const status = session?.status;
  const expired = status === 'expired' || (secondsRemaining <= 0 && status === 'active');
  const submitted = status === 'submitted';

  // Ticks for the display only; every tick recomputes from the deadline rather
  // than subtracting one, so a suspended tab resumes at the right number.
  useEffect(() => {
    if (!expiresAt || submitted) return;
    const tick = () => setSecondsRemaining(secondsUntil(expiresAt));
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [expiresAt, submitted]);

  // The server is asked again periodically, so a deadline moved or a sitting
  // settled elsewhere reaches this screen without a reload.
  useEffect(() => {
    if (submitted) return;
    const timer = setInterval(() => {
      void quizAPI
        .getSession(courseId, quizId, sessionId)
        .then((next) => setSession(next))
        .catch(() => undefined);
    }, RECONCILE_MS);
    return () => clearInterval(timer);
  }, [courseId, quizId, sessionId, submitted]);

  const write = useCallback(
    async (question: QuizQuestionView, draft: AnswerDraft) => {
      const answer: QuizAnswerSubmission = isOptionBased(question.question_type)
        ? { question_id: question.question_id, selected_option_index: draft.optionIndex }
        : { question_id: question.question_id, text_response: draft.text.trim() || null };

      const request = quizAPI
        .saveSessionAnswer(courseId, quizId, sessionId, question.question_id, answer)
        .then((next) => {
          setSession(next);
          setSaveError(null);
        })
        .catch((error: unknown) => {
          setSaveError(describeGenerationError(error, 'That answer could not be saved.'));
        });

      inFlight.current.push(request);
      await request;
      inFlight.current = inFlight.current.filter((entry) => entry !== request);
    },
    [courseId, quizId, sessionId],
  );

  const setAnswer = useCallback(
    (question: QuizQuestionView, draft: AnswerDraft) => {
      setAnswers((current) => ({ ...current, [question.question_id]: draft }));

      const existing = pending.current.get(question.question_id);
      if (existing) clearTimeout(existing.timer);

      // A chosen option is one decisive act, so it is written at once. Writing
      // is debounced only where the candidate is still mid-sentence.
      if (isOptionBased(question.question_type)) {
        pending.current.delete(question.question_id);
        void write(question, draft);
        return;
      }

      pending.current.set(question.question_id, {
        question,
        draft,
        timer: setTimeout(() => {
          pending.current.delete(question.question_id);
          void write(question, draft);
        }, WRITE_DEBOUNCE_MS),
      });
    },
    [write],
  );

  /**
   * Send everything still on a debounce, then wait for every write to land.
   *
   * Cancelling the timers alone would drop the last thing typed, which is the
   * one answer a candidate is most likely to be part-way through when they
   * hand in.
   */
  const flush = useCallback(async () => {
    const waiting = [...pending.current.values()];
    for (const entry of waiting) clearTimeout(entry.timer);
    pending.current.clear();

    await Promise.all(waiting.map((entry) => write(entry.question, entry.draft)));
    await Promise.all(inFlight.current);
  }, [write]);

  useEffect(
    () => () => {
      for (const [, entry] of pending.current) clearTimeout(entry.timer);
      pending.current.clear();
    },
    [],
  );

  return {
    session,
    answers,
    secondsRemaining,
    expired,
    submitted,
    saveError,
    setAnswer,
    flush,
  };
}

export { draftsFrom, secondsUntil };
