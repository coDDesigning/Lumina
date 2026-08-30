import { useState } from 'react';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useAuth } from '@/context/AuthContext';
import { QuizModal } from '@/features/study/quiz/QuizModal';
import { Alert } from '@/ui/Alert';
import { Badge } from '@/ui/Badge';
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
  const { user } = useAuth();
  const courseId = Number(workspace.id);
  useDocumentTitle(`${workspace.name} · Progress`);

  const [practiceTopic, setPracticeTopic] = useState<string | null>(null);

  const { entries, readyCount } = useCourseDocuments(courseId);
  const { progress, isLoading, error, reload } = useCourseProgress(courseId);

  const isSupportView = Boolean(user && workspace.ownerId != null && workspace.ownerId !== user.id);
  const ownerDisplayName =
    workspace.ownerName ||
    workspace.ownerEmail ||
    (workspace.ownerId ? `User #${workspace.ownerId}` : 'another user');

  return (
    <div className={styles.page}>
      <PageHeader
        courseId={workspace.id}
        crumbs={[
          { label: 'Courses', to: '/dashboard' },
          { label: workspace.name, to: `/courses/${workspace.id}` },
          { label: 'Progress' },
        ]}
        badges={isSupportView ? <Badge tone="accent">Read-Only Support</Badge> : null}
      />

      {isSupportView ? (
        <div className={styles.supportBanner}>
          <Alert tone="info">
            <strong>Read-Only Support View</strong> — Viewing progress for course owned by{' '}
            <strong>{ownerDisplayName}</strong>.
          </Alert>
        </div>
      ) : null}

      <h1 className="visually-hidden">{workspace.name} progress</h1>

      <div className={styles.body}>
        <ProgressView
          courseId={workspace.id}
          documentCount={entries.length}
          readyDocumentCount={readyCount}
          progress={progress}
          isLoading={isLoading}
          error={error}
          onPractice={!isSupportView ? setPracticeTopic : undefined}
          onRetry={reload}
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
          onQueued={() => setPracticeTopic(null)}
          onClose={() => setPracticeTopic(null)}
          onAttemptRecorded={reload}
        />
      ) : null}
    </div>
  );
}
