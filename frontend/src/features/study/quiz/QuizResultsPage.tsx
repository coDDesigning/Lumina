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
  const { quizId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const courseId = Number(workspace.id);

  // Handing a quiz in navigates here with the marked attempt already in hand; a cold
  // load has only the URL, and the questions still have to be fetched to show a review.
  const handedIn = (location.state as HandedIn | null) ?? null;

  const [quiz, setQuiz] = useState<QuizView | null>(handedIn?.quiz ?? null);
  const [error, setError] = useState<string | null>(null);

  useDocumentTitle(`Quiz results · ${workspace.name}`);

  useEffect(() => {
    if (quiz) {
      return;
    }
    const controller = new AbortController();
    const id = Number(quizId);

    if (!Number.isInteger(id) || id <= 0) {
      setError('That is not a quiz address.');
      return;
    }

    quizAPI
      .get(courseId, id, { signal: controller.signal })
      .then((loaded) => {
        if (!controller.signal.aborted) {
          setQuiz(loaded);
        }
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted || isAbortError(caught)) {
          return;
        }
        setError(describeError(caught, 'This quiz could not be opened.').message);
      });

    return () => controller.abort();
  }, [courseId, quiz, quizId]);

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

        {!handedIn && !error ? (
          <Alert tone="info" title="Opening a past attempt is not supported yet">
            The score for this attempt is on your progress page. Reviewing an individual past
            attempt question by question needs an endpoint the backend does not serve yet.
          </Alert>
        ) : null}

        {handedIn && !quiz ? (
          <div className={styles.pending}>
            <Skeleton variant="heading" />
            <Skeleton variant="block" />
          </div>
        ) : null}

        {handedIn && quiz ? (
          <QuizResults attempt={handedIn.attempt} questions={quiz.questions} />
        ) : null}
      </div>
    </div>
  );
}
