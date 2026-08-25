import { useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { RotateCcw } from 'lucide-react';
import { quizAPI } from '@/api/quiz';
import { queryKeys } from '@/api/queryKeys';
import { useQuery } from '@/lib/query/useQuery';
import type { QuizAttemptResponse, QuizView } from '@/api/types';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import type { Workspace } from '@/data/workspaces';
import { Button } from '@/ui/Button';
import { ErrorState } from '@/ui/ErrorState';
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

  const parsedQuizId = Number(quizId);
  const parsedAttemptId = Number(attemptId);
  const isValidAddress =
    Number.isInteger(parsedQuizId) &&
    parsedQuizId > 0 &&
    Number.isInteger(parsedAttemptId) &&
    parsedAttemptId > 0;
  const wasHandedIn = Boolean(handedIn?.quiz && handedIn?.attempt);

  const attemptQuery = useQuery<{ quiz: QuizView; attempt: QuizAttemptResponse }>({
    key:
      !wasHandedIn && isValidAddress
        ? queryKeys.courseQuizAttempt(courseId, parsedQuizId, parsedAttemptId)
        : null,
    fetcher: async ({ signal }) => {
      const [loadedQuiz, loadedAttempt] = await Promise.all([
        quizAPI.get(courseId, parsedQuizId, { signal }),
        quizAPI.getAttempt(courseId, parsedQuizId, parsedAttemptId, { signal }),
      ]);
      return { quiz: loadedQuiz, attempt: loadedAttempt };
    },
    fallbackMessage: 'This attempt could not be opened.',
    staleTime: 5 * 60_000,
  });

  useEffect(() => {
    if (wasHandedIn) {
      return;
    }
    if (!isValidAddress) {
      setError('That is not a valid quiz attempt address.');
      setIsLoading(false);
      return;
    }
    if (attemptQuery.status === 'pending' || attemptQuery.status === 'idle') {
      setIsLoading(true);
      return;
    }
    setIsLoading(false);
    if (attemptQuery.status === 'error') {
      setError(attemptQuery.error?.message ?? 'This attempt could not be opened.');
      return;
    }
    if (attemptQuery.data) {
      setError(null);
      setQuiz(attemptQuery.data.quiz);
      setAttempt(attemptQuery.data.attempt);
    }
  }, [
    wasHandedIn,
    isValidAddress,
    attemptQuery.status,
    attemptQuery.data,
    attemptQuery.error,
  ]);

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
          <ErrorState
            title="These results are not here"
            actions={
              <Button size="sm" onClick={() => navigate(`/courses/${workspace.id}`)}>
                Back to the course
              </Button>
            }
          >
            {error}
          </ErrorState>
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

