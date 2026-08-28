import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Send } from 'lucide-react';
import { describeError } from '@/api/errors';
import { quizAPI } from '@/api/quiz';
import { queryKeys } from '@/api/queryKeys';
import { useQuery } from '@/lib/query/useQuery';
import { afterQuizAttempt } from '@/api/invalidations';
import type { QuizQuestionView, QuizView } from '@/api/types';
import { isOptionBased } from '@/api/types';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import type { Workspace } from '@/data/workspaces';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { ConfirmDialog } from '@/ui/ConfirmDialog';
import { ErrorState } from '@/ui/ErrorState';
import { PageHeader } from '@/ui/PageHeader';
import { Skeleton } from '@/ui/Skeleton';
import { EMPTY_DRAFT } from './answerDraft';
import type { AnswerDraft } from './answerDraft';
import { QuizAnswerField } from './QuizAnswerField';
import { QuizNavigator } from './QuizNavigator';
import { isTimed, purposeLabel, quizReturn } from './quizPurpose';
import styles from './QuizAttemptPage.module.css';

export interface QuizAttemptPageProps {
  workspace: Workspace;
  onAttemptRecorded?: () => void;
}

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
  const [isSubmitting, setIsSubmitting] = useState(false);

  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isConfirming, setIsConfirming] = useState(false);

  const startedAtRef = useRef(0);
  useDocumentTitle(`Quiz · ${workspace.name}`);

  const quizIdNumber = Number(quizId);
  const isValidQuizAddress = Number.isInteger(quizIdNumber) && quizIdNumber > 0;

  const quizQuery = useQuery<QuizView>({
    key: isValidQuizAddress ? queryKeys.courseQuiz(courseId, quizIdNumber) : null,
    fetcher: ({ signal }) => quizAPI.get(courseId, quizIdNumber, { signal }),
    fallbackMessage: 'This quiz could not be opened.',
    staleTime: 5 * 60_000,
  });

  useEffect(() => {
    if (!isValidQuizAddress) {
      setLoadError('That is not a quiz address.');
      return;
    }
    if (quizQuery.status === 'error') {
      setLoadError(quizQuery.error?.message ?? 'This quiz could not be opened.');
      return;
    }
    const loaded = quizQuery.data;
    if (!loaded) {
      return;
    }
    setLoadError(null);
    setQuiz(loaded);
    startedAtRef.current = Date.now();
  }, [isValidQuizAddress, quizQuery.status, quizQuery.data, quizQuery.error]);

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
      afterQuizAttempt(courseId);
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

  const [isStarting, setIsStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  /**
   * Open the sitting, then go to it.
   *
   * The clock is the server's and starts here -- not when the paper was
   * written, and not while the candidate is still reading what it involves.
   */
  const begin = useCallback(async () => {
    if (!quiz) return;
    setIsStarting(true);
    setStartError(null);
    try {
      const started = await quizAPI.startSession(courseId, quiz.quiz_id);
      navigate(
        `/courses/${workspace.id}/practice/${quiz.quiz_id}/sessions/${started.session.session_id}`,
        { replace: true },
      );
    } catch (caught) {
      setStartError(describeError(caught, 'That sitting could not be started.').message);
      setIsStarting(false);
    }
  }, [courseId, navigate, quiz, workspace.id]);

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
          { label: quiz ? purposeLabel(quiz.quiz_purpose) : 'Quiz' },
        ]}
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

        {quiz && isTimed(quiz) ? (
          <div className={styles.instructions}>
            <h1 className={styles.instructionsTitle}>{quiz.title}</h1>
            <p className={styles.instructionsLede}>
              {purposeLabel(quiz.quiz_purpose)} · <span className="tabular">
                {questions.length}
              </span>{' '}
              {questions.length === 1 ? 'question' : 'questions'}
              {quiz.time_limit_seconds ? (
                <>
                  {' '}
                  · <span className="tabular">{Math.round(quiz.time_limit_seconds / 60)}</span>{' '}
                  minutes
                </>
              ) : null}
            </p>
            <ul className={styles.instructionsList}>
              <li>The clock starts when you begin, and it is kept by the server.</li>
              <li>Answers are saved as you give them, so a reload picks up where you left off.</li>
              <li>
                When time runs out you cannot answer any more, but everything already saved is
                still marked.
              </li>
            </ul>
            {startError ? (
              <Alert tone="destructive" live="alert">
                {startError}
              </Alert>
            ) : null}
            <div className={styles.controls}>
              <Button
                variant="primary"
                isLoading={isStarting}
                loadingLabel="Opening your paper"
                onClick={() => void begin()}
              >
                Begin the timed paper
              </Button>
              <Button
                variant="secondary"
                onClick={() => navigate(quizReturn(courseId, quiz).to)}
              >
                Not yet
              </Button>
            </div>
          </div>
        ) : null}

        {quiz && !isTimed(quiz) && question ? (
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

            <QuizNavigator
              questions={questions}
              index={index}
              onIndex={setIndex}
              isAnswered={(questionId) => isAnswered(answers[questionId])}
            />

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
