import { useCallback, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Send } from 'lucide-react';
import { describeGenerationError } from '@/api/errors';
import type { GenerationFailure } from '@/api/errors';
import { afterTimedSessionSubmitted } from '@/api/invalidations';
import { quizAPI } from '@/api/quiz';
import { queryKeys } from '@/api/queryKeys';
import type { QuizSessionView, QuizView } from '@/api/types';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import type { Workspace } from '@/data/workspaces';
import { useQuery } from '@/lib/query/useQuery';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { ConfirmDialog } from '@/ui/ConfirmDialog';
import { EmptyState } from '@/ui/EmptyState';
import { ErrorState } from '@/ui/ErrorState';
import { LinkButton } from '@/ui/LinkButton';
import { PageHeader } from '@/ui/PageHeader';
import { Skeleton } from '@/ui/Skeleton';
import { EMPTY_DRAFT } from './answerDraft';
import { ExamTimer } from './ExamTimer';
import { QuizAnswerField } from './QuizAnswerField';
import { QuizNavigator } from './QuizNavigator';
import { purposeLabel } from './quizPurpose';
import { useTimedSession } from './useTimedSession';
import styles from './QuizSessionPage.module.css';

export interface QuizSessionPageProps {
  workspace: Workspace;
}

const QUESTION_TYPE_LABELS: Record<string, string> = {
  multiple_choice: 'Multiple choice',
  true_false: 'True or false',
  short_answer: 'Short answer',
  open_ended: 'Written answer',
};

/**
 * Sitting a timed paper.
 *
 * The clock, the drafts, and the decision that a sitting is over all belong to
 * the server. This screen shows them and writes answers as they are given.
 */
export default function QuizSessionPage({ workspace }: QuizSessionPageProps) {
  const { quizId, sessionId } = useParams();
  const navigate = useNavigate();
  const courseId = Number(workspace.id);
  const quizIdNumber = Number(quizId);
  const sessionIdNumber = Number(sessionId);
  const valid =
    Number.isInteger(quizIdNumber) &&
    quizIdNumber > 0 &&
    Number.isInteger(sessionIdNumber) &&
    sessionIdNumber > 0;

  const [index, setIndex] = useState(0);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isConfirming, setIsConfirming] = useState(false);
  const [submitError, setSubmitError] = useState<GenerationFailure | null>(null);

  const quizQuery = useQuery<QuizView>({
    key: valid ? queryKeys.courseQuiz(courseId, quizIdNumber) : null,
    fetcher: ({ signal }) => quizAPI.get(courseId, quizIdNumber, { signal }),
    fallbackMessage: 'This paper could not be opened.',
    staleTime: 5 * 60_000,
  });

  const sessionQuery = useQuery<QuizSessionView>({
    key: valid ? queryKeys.courseQuizSession(courseId, quizIdNumber, sessionIdNumber) : null,
    fetcher: ({ signal }) =>
      quizAPI.getSession(courseId, quizIdNumber, sessionIdNumber, { signal }),
    fallbackMessage: 'This sitting could not be opened.',
  });

  const sitting = useTimedSession(
    courseId,
    quizIdNumber,
    sessionIdNumber,
    sessionQuery.data ?? null,
  );

  const quiz = quizQuery.data;
  const questions = useMemo(() => quiz?.questions ?? [], [quiz]);
  useDocumentTitle(quiz ? `${purposeLabel(quiz.quiz_purpose)} · ${workspace.name}` : undefined);

  const answered = useCallback(
    (questionId: number) => {
      const draft = sitting.answers[questionId];
      return Boolean(draft && (draft.optionIndex !== null || draft.text.trim().length > 0));
    },
    [sitting.answers],
  );

  const answeredCount = questions.filter((row) => answered(row.question_id)).length;
  const unanswered = questions.length - answeredCount;

  const hand = useCallback(async () => {
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      // Nothing is handed in with a keystroke still on its debounce.
      await sitting.flush();
      const attempt = await quizAPI.submitSession(courseId, quizIdNumber, sessionIdNumber);
      afterTimedSessionSubmitted(courseId, quizIdNumber, sessionIdNumber);
      navigate(
        `/courses/${workspace.id}/practice/${quizIdNumber}/attempts/${attempt.attempt_id}`,
        { replace: true, state: { attempt, quiz } },
      );
    } catch (error) {
      setSubmitError(describeGenerationError(error, 'Your paper could not be handed in.'));
      setIsSubmitting(false);
    }
  }, [courseId, navigate, quiz, quizIdNumber, sessionIdNumber, sitting, workspace.id]);

  const header = (
    <PageHeader
      courseId={workspace.id}
      crumbs={[
        { label: 'Courses', to: '/dashboard' },
        { label: workspace.name, to: `/courses/${workspace.id}` },
        { label: quiz ? purposeLabel(quiz.quiz_purpose) : 'Timed paper' },
      ]}
      actions={
        sitting.session && !sitting.submitted ? (
          <ExamTimer secondsRemaining={sitting.secondsRemaining} expired={sitting.expired} />
        ) : null
      }
    />
  );

  if (!valid) {
    return (
      <div className={styles.page}>
        {header}
        <div className={styles.body}>
          <h1 className="visually-hidden">Timed paper</h1>
          <EmptyState
            title="That sitting is not available"
            description="The link does not name a sitting of this paper."
            actions={<LinkButton to={`/courses/${courseId}`}>Back to the course</LinkButton>}
          />
        </div>
      </div>
    );
  }

  const failed =
    quizQuery.status === 'error'
      ? quizQuery.error
      : sessionQuery.status === 'error'
        ? sessionQuery.error
        : null;

  if (failed) {
    return (
      <div className={styles.page}>
        {header}
        <div className={styles.body}>
          <h1 className="visually-hidden">Timed paper</h1>
          {failed.status === 404 ? (
            <EmptyState
              title="That sitting is not available"
              description="It may no longer exist, or it may not be one of yours."
              actions={<LinkButton to={`/courses/${courseId}`}>Back to the course</LinkButton>}
            />
          ) : (
            <ErrorState
              title="This sitting could not be opened"
              onRetry={() => {
                void quizQuery.refetch();
                void sessionQuery.refetch();
              }}
            >
              {failed.message}
            </ErrorState>
          )}
        </div>
      </div>
    );
  }

  if (!quiz || !sitting.session) {
    return (
      <div className={styles.page}>
        {header}
        <div className={styles.body}>
          <h1 className="visually-hidden">Timed paper</h1>
          <div className={styles.pending} role="status" aria-label="Opening your paper">
            <Skeleton variant="heading" />
            <Skeleton variant="block" height="12rem" />
          </div>
        </div>
      </div>
    );
  }

  // A finished sitting is an ordinary attempt, and that is where its result is.
  if (sitting.submitted && sitting.session.attempt_id) {
    return (
      <div className={styles.page}>
        {header}
        <div className={styles.body}>
          <h1 className="visually-hidden">Timed paper</h1>
          <EmptyState
            title="You have already handed this in"
            description="It has been marked."
            actions={
              <LinkButton
                to={`/courses/${courseId}/practice/${quizIdNumber}/attempts/${sitting.session.attempt_id}`}
              >
                See how it went
              </LinkButton>
            }
          />
        </div>
      </div>
    );
  }

  const question = questions[index];
  const locked = sitting.expired;

  return (
    <div className={styles.page}>
      {header}
      <div className={styles.body}>
        <h1 className="visually-hidden">
          {purposeLabel(quiz.quiz_purpose)}: {quiz.title}
        </h1>

        {locked ? (
          <Alert tone="warning" live="status" title="Time is up">
            Every answer you saved is still here and will be marked. You cannot change them now.
          </Alert>
        ) : null}

        {sitting.saveError ? (
          <Alert tone="destructive" live="alert" title={sitting.saveError.title}>
            {sitting.saveError.message}
          </Alert>
        ) : null}

        <div className={styles.head}>
          <p className={styles.position}>
            Question <span className="tabular">{index + 1}</span> of{' '}
            <span className="tabular">{questions.length}</span>
          </p>
          <p className={styles.answered}>
            <span className="tabular">{answeredCount}</span> saved
          </p>
        </div>

        <QuizNavigator
          questions={questions}
          index={index}
          onIndex={setIndex}
          isAnswered={answered}
        />

        {question ? (
          <div className={styles.card}>
            <div className={styles.meta}>
              <span>
                {QUESTION_TYPE_LABELS[question.question_type] ?? question.question_type}
              </span>
              {question.topic ? <span>{question.topic}</span> : null}
            </div>
            <p className={styles.question}>{question.question}</p>

            <QuizAnswerField
              question={question}
              draft={sitting.answers[question.question_id] ?? EMPTY_DRAFT}
              disabled={locked}
              onChange={(draft) => sitting.setAnswer(question, draft)}
              onBlur={() => void sitting.flush()}
            />
          </div>
        ) : null}

        {submitError ? (
          <ErrorState title={submitError.title} onRetry={() => void hand()}>
            {submitError.message} Your saved answers are safe.
          </ErrorState>
        ) : null}

        <div className={styles.controls}>
          <Button
            onClick={() => {
              void sitting.flush();
              setIndex((current) => current - 1);
            }}
            disabled={index === 0}
          >
            Previous
          </Button>
          {index === questions.length - 1 || locked ? (
            <Button
              variant="primary"
              icon={<Send aria-hidden="true" />}
              isLoading={isSubmitting}
              loadingLabel="Handing it in"
              onClick={() => (!locked && unanswered > 0 ? setIsConfirming(true) : void hand())}
            >
              {locked ? 'Hand in what you saved' : 'Hand it in'}
            </Button>
          ) : (
            <Button
              variant="primary"
              onClick={() => {
                void sitting.flush();
                setIndex((current) => current + 1);
              }}
            >
              Next question
            </Button>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={isConfirming}
        onClose={() => setIsConfirming(false)}
        onConfirm={() => {
          setIsConfirming(false);
          void hand();
        }}
        title="Hand it in unfinished?"
        confirmLabel="Hand it in"
        cancelLabel="Keep working"
      >
        {unanswered === 1
          ? 'One question has no saved answer, and an unanswered question is marked wrong.'
          : `${unanswered} questions have no saved answer, and an unanswered question is marked wrong.`}
      </ConfirmDialog>
    </div>
  );
}
