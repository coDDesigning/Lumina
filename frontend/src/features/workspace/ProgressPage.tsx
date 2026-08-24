import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { QuizModal } from '@/features/study/quiz/QuizModal';
import { PastQuizzes } from './PastQuizzes';
import { ProgressView } from './ProgressView';
import type { Workspace } from '@/data/workspaces';
import { useCourseDocuments } from '@/hooks/useCourseDocuments';
import { LinkButton } from '@/ui/LinkButton';
import { PageHeader } from '@/ui/PageHeader';
import { useCourseProgress } from './useCourseProgress';
import styles from './ProgressPage.module.css';

export interface ProgressPageProps {
  workspace: Workspace;
}

export default function ProgressPage({ workspace }: ProgressPageProps) {
  const courseId = Number(workspace.id);
  useDocumentTitle(`${workspace.name} · Progress`);

  const navigate = useNavigate();
  const [practiceTopic, setPracticeTopic] = useState<string | null>(null);

  const { entries, readyCount } = useCourseDocuments(courseId);
  const { progress, isLoading, error, reload } = useCourseProgress(courseId);

  return (
    <div className={styles.page}>
      <PageHeader
        courseId={workspace.id}
        crumbs={[
          { label: 'Courses', to: '/dashboard' },
          { label: workspace.name, to: `/courses/${workspace.id}` },
          { label: 'Progress' },
        ]}
      />

      <div className={styles.body}>
        <ProgressView
          courseId={workspace.id}
          documentCount={entries.length}
          readyDocumentCount={readyCount}
          progress={progress}
          isLoading={isLoading}
          error={error}
          onPractice={setPracticeTopic}
          actions={
            <LinkButton variant="primary" to={`/courses/${workspace.id}`}>
              Back to the course
            </LinkButton>
          }
        />

        <PastQuizzes courseId={courseId} workspaceId={workspace.id} />
      </div>

      {practiceTopic !== null ? (
        <QuizModal
          courseId={courseId}
          topics={workspace.topics}
          readyDocumentCount={readyCount}
          initialTopic={practiceTopic}
          onQuizReady={(quizId) => {
            setPracticeTopic(null);
            navigate(`/courses/${workspace.id}/practice/${quizId}`);
          }}
          onClose={() => setPracticeTopic(null)}
          onAttemptRecorded={reload}
        />
      ) : null}
    </div>
  );
}
