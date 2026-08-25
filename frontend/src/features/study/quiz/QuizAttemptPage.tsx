import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Clock, Send } from 'lucide-react';
import { describeError, isAbortError } from '@/api/errors';
import { quizAPI } from '@/api/quiz';
import type { QuizQuestionView, QuizView } from '@/api/types';
import { isOptionBased } from '@/api/types';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import type { Workspace } from '@/data/workspaces';
import { cx } from '@/lib/cx';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { ConfirmDialog } from '@/ui/ConfirmDialog';
import { ErrorState } from '@/ui/ErrorState';
import { PageHeader } from '@/ui/PageHeader';
import { Skeleton } from '@/ui/Skeleton';
import { EMPTY_DRAFT } from './answerDraft';
import type { AnswerDraft } from './answerDraft';
import { QuizAnswerField } from './QuizAnswerField';
import { formatDuration } from './quizScoring';
import styles from './QuizAttemptPage.module.css';

export interface QuizAttemptPageProps {
  workspace: Workspace;
  onAttemptRecorded?: () => void;
}

const SECONDS_PER_QUESTION = 60;
const LOW_TIME_SECONDS = 60;

const QUESTION_TYPE_LABELS: Record<string, string> = {
  multiple_choice: 'Multiple choice',
  true_false: 'True or false',
  short_answer: 'Short answer',
  open_ended: 'Written answer',
};

function isAnswered(draft: AnswerDraft | undefined): boolean {
  if (!draft) {
    return false;
  }
  return draft.optionIndex !== null || draft.text.trim().length > 0;
}

export default function QuizAttemptPage({ workspace, onAttemptRecorded }: QuizAttemptPageProps) {
  const { quizId } = useParams();
  const navigate = useNavigate();
  const courseId = Number(workspace.id);

  const [quiz, setQuiz] = useState<QuizView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<number, AnswerDraft>>({});
  const [timeLeft, setTimeLeft] = useState(0);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isConfirming, setIsConfirming] = useState(false);

  const startedAtRef = useRef(0);
  useDocumentTitle(`Quiz · ${workspace.name}`);

  useEffect(() => {
    const controller = new AbortController();
    const id = Number(quizId);

    if (!Number.isInteger(id) || id <= 0) {
      setLoadError('That is not a quiz address.');
      return;
    }

    quizAPI
      .get(courseId, id, { signal: controller.signal })
      .then((loaded) => {
        if (controller.signal.aborted) {
          return;
        }
        setQuiz(loaded);
        setTimeLeft(loaded.questions.length * SECONDS_PER_QUESTION);
        startedAtRef.current = Date.now();
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted || isAbortError(caught)) {
          return;
        }
        setLoadError(describeError(caught, 'This quiz could not be opened.').message);
      });

    return () => controller.abort();
  }, [courseId, quizId]);

  const questions: QuizQuestionView[] = useMemo(() => quiz?.questions ?? [], [quiz]);

  const submit = useCallback(async () => {
    if (!quiz || isSubmitting) {
      return;
    }
    setIsSubmitting(true);
    setSubmitError(null);

    const spent = Math.max(1, Math.round((Date.now() - startedAtRef.current) / 1000));

    try {
      const recorded = await quizAPI.submitAttempt(courseId, quiz.quiz_id, {
        answers: quiz.questions.map((question) => {
          const draft = answers[question.question_id] ?? EMPTY_DRAFT;
          if (isOptionBased(question.question_type)) {
            return {
              question_id: question.question_id,
              selected_option_index: draft.optionIndex,
            };
          }
          return {
            question_id: question.question_id,
            text_response: draft.text.trim() || null,
          };
        }),
        time_spent_seconds: spent,
      });
      onAttemptRecorded?.();
      navigate(
        `/courses/${workspace.id}/practice/${quiz.quiz_id}/attempts/${recorded.attempt_id}`,
        { replace: true, state: { attempt: recorded, quiz } },
      );
    } catch (caught) {
      setSubmitError(describeError(caught, 'Your answers could not be saved.').message);
      setIsSubmitting(false);
    }
  }, [answers, courseId, isSubmitting, navigate, onAttemptRecorded, quiz, workspace.id]);

  useEffect(() => {
    if (!quiz || isSubmitting) {
      return;
    }
    if (timeLeft <= 0) {
      void submit();
      return;
    }
    const timer = setTimeout(() => setTimeLeft((seconds) => seconds - 1), 1000);
    return () => clearTimeout(timer);
  }, [quiz, isSubmitting, timeLeft, submit]);

  const question = questions[index];
  const answeredCount = questions.filter((row) => isAnswered(answers[row.question_id])).length;
  const unanswered = questions.length - answeredCount;
  const isLast = index === questions.length - 1;

  return (
    <div className={styles.page}>
      <PageHeader
        courseId={workspace.id}
        crumbs={[
          { label: 'Courses', to: '/dashboard' },
          { label: workspace.name, to: `/courses/${workspace.id}` },
          { label: 'Quiz' },
        ]}
        actions={
          quiz ? (
            <p
              className={cx(styles.timer, timeLeft <= LOW_TIME_SECONDS && styles.timerLow)}
              role="timer"
              aria-label={`Time remaining: ${formatDuration(timeLeft)}`}
            >
              <Clock aria-hidden="true" />
              <span className="tabular">{formatDuration(timeLeft)}</span>
            </p>
          ) : null
        }
      />

      <div className={styles.body}>
        {loadError ? (
          <ErrorState
            title="This quiz is not here"
            actions={
              <Button size="sm" onClick={() => navigate(`/courses/${workspace.id}`)}>
                Back to the course
              </Button>
            }
          >
            {loadError}
          </ErrorState>
        ) : null}

        {!quiz && !loadError ? (
          <div className={styles.pending}>
            <Skeleton variant="heading" />
            <Skeleton variant="block" />
          </div>
        ) : null}

        {quiz && question ? (
          <>
            <div className={styles.head}>
              <p className={styles.position}>
                Question <span className="tabular">{index + 1}</span> of{' '}
                <span className="tabular">{questions.length}</span>
              </p>
              <p className={styles.answered}>
                <span className="tabular">{answeredCount}</span> answered
              </p>
            </div>

            <nav className={styles.navigator} aria-label="Questions">
              {questions.map((row, position) => (
                <button
                  key={row.question_id}
                  type="button"
                  className={cx(
                    styles.pip,
                    position === index && styles.pipCurrent,
                    isAnswered(answers[row.question_id]) && styles.pipAnswered,
                  )}
                  aria-current={position === index ? 'true' : undefined}
                  aria-label={`Question ${position + 1}${
                    isAnswered(answers[row.question_id]) ? ', answered' : ', not answered'
                  }`}
                  onClick={() => setIndex(position)}
                >
                  <span aria-hidden="true">{position + 1}</span>
                </button>
              ))}
            </nav>

            <div className={styles.card}>
              <div className={styles.meta}>
                <span>{QUESTION_TYPE_LABELS[question.question_type] ?? question.question_type}</span>
                {question.topic ? <span>{question.topic}</span> : null}
              </div>
              <h1 className={styles.question}>{question.question}</h1>

              <QuizAnswerField
                question={question}
                draft={answers[question.question_id] ?? EMPTY_DRAFT}
                onChange={(draft) =>
                  setAnswers((previous) => ({ ...previous, [question.question_id]: draft }))
                }
              />
            </div>

            {submitError ? (
              <Alert tone="destructive" live="alert">
                {submitError}
              </Alert>
            ) : null}

            <div className={styles.controls}>
              <Button onClick={() => setIndex((current) => current - 1)} disabled={index === 0}>
                Previous
              </Button>
              {isLast ? (
                <Button
                  variant="primary"
                  onClick={() => (unanswered > 0 ? setIsConfirming(true) : void submit())}
                  isLoading={isSubmitting}
                  loadingLabel="Marking your answers"
                  icon={<Send aria-hidden="true" />}
                >
                  Hand it in
                </Button>
              ) : (
                <Button variant="primary" onClick={() => setIndex((current) => current + 1)}>
                  Next question
                </Button>
              )}
            </div>
          </>
        ) : null}
      </div>

      <ConfirmDialog
        open={isConfirming}
        onClose={() => setIsConfirming(false)}
        onConfirm={() => {
          setIsConfirming(false);
          void submit();
        }}
        title="Hand it in unfinished?"
        confirmLabel="Hand it in"
        cancelLabel="Keep working"
      >
        {unanswered === 1
          ? 'One question is still unanswered, and an unanswered question is marked wrong.'
          : `${unanswered} questions are still unanswered, and an unanswered question is marked wrong.`}
      </ConfirmDialog>
    </div>
  );
}
