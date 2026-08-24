import { useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { RotateCcw } from 'lucide-react';
import { describeError, isAbortError } from '@/api/errors';
import { quizAPI } from '@/api/quiz';
import type { QuizAttemptResponse, QuizView } from '@/api/types';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import type { Workspace } from '@/data/workspaces';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { LinkButton } from '@/ui/LinkButton';
import { PageHeader } from '@/ui/PageHeader';
import { Skeleton } from '@/ui/Skeleton';
import { QuizResults } from './QuizResults';
import styles from './QuizAttemptPage.module.css';

export interface QuizResultsPageProps {
  workspace: Workspace;
}

interface HandedIn {
  attempt: QuizAttemptResponse;
  quiz: QuizView;
}

export default function QuizResultsPage({ workspace }: QuizResultsPageProps) {
  const { quizId, attemptId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const courseId = Number(workspace.id);

  // Handing a quiz in navigates here with the marked attempt already in hand; a cold
  // load has only the URL, and the questions and attempt are fetched to show a review.
  const handedIn = (location.state as HandedIn | null) ?? null;

  const [quiz, setQuiz] = useState<QuizView | null>(handedIn?.quiz ?? null);
  const [attempt, setAttempt] = useState<QuizAttemptResponse | null>(
    handedIn?.attempt ?? null,
  );
  const [isLoading, setIsLoading] = useState<boolean>(!handedIn);
  const [error, setError] = useState<string | null>(null);

  useDocumentTitle(`Quiz results · ${workspace.name}`);

  useEffect(() => {
    if (handedIn?.quiz && handedIn?.attempt) {
      return;
    }

    const controller = new AbortController();
    const parsedQuizId = Number(quizId);
    const parsedAttemptId = Number(attemptId);

    if (
      !Number.isInteger(parsedQuizId) ||
      parsedQuizId <= 0 ||
      !Number.isInteger(parsedAttemptId) ||
      parsedAttemptId <= 0
    ) {
      setError('That is not a valid quiz attempt address.');
      setIsLoading(false);
      return;
    }

    setIsLoading(true);
    setError(null);

    Promise.all([
      quizAPI.get(courseId, parsedQuizId, { signal: controller.signal }),
      quizAPI.getAttempt(courseId, parsedQuizId, parsedAttemptId, {
        signal: controller.signal,
      }),
    ])
      .then(([loadedQuiz, loadedAttempt]) => {
        if (!controller.signal.aborted) {
          setQuiz(loadedQuiz);
          setAttempt(loadedAttempt);
          setIsLoading(false);
        }
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted || isAbortError(caught)) {
          return;
        }
        setIsLoading(false);
        setError(describeError(caught, 'This attempt could not be opened.').message);
      });

    return () => controller.abort();
  }, [courseId, handedIn, quizId, attemptId]);

  return (
    <div className={styles.page}>
      <PageHeader
        courseId={workspace.id}
        crumbs={[
          { label: 'Courses', to: '/dashboard' },
          { label: workspace.name, to: `/courses/${workspace.id}` },
          { label: 'Quiz results' },
        ]}
        actions={
          <>
            <LinkButton to={`/courses/${workspace.id}/progress`}>See your progress</LinkButton>
            <Button
              variant="primary"
              onClick={() => navigate(`/courses/${workspace.id}`)}
              icon={<RotateCcw aria-hidden="true" />}
            >
              Another quiz
            </Button>
          </>
        }
      />

      <div className={styles.body}>
        {error ? (
          <Alert tone="destructive" live="alert" title="These results are not here">
            {error}
          </Alert>
        ) : null}

        {isLoading && !error ? (
          <div className={styles.pending}>
            <Skeleton variant="heading" />
            <Skeleton variant="block" />
          </div>
        ) : null}

        {!isLoading && !error && quiz && attempt ? (
          <QuizResults attempt={attempt} questions={quiz.questions} />
        ) : null}
      </div>
    </div>
  );
}

