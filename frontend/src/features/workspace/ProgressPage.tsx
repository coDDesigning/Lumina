import { useDocumentTitle } from '@/app/useDocumentTitle';
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

  const { entries, readyCount } = useCourseDocuments(courseId);
  const { progress, isLoading, error } = useCourseProgress(courseId);

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
          actions={
            <LinkButton variant="primary" to={`/courses/${workspace.id}`}>
              Back to the course
            </LinkButton>
          }
        />

        <PastQuizzes courseId={courseId} workspaceId={workspace.id} />
      </div>
    </div>
  );
}
