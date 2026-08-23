import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ProgressDashboard } from '@/components/study/ProgressDashboard';
import type { Workspace } from '@/data/workspaces';
import { useCourseDocuments } from '@/hooks/useCourseDocuments';
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
          { label: workspace.name, to: `/workspaces/${workspace.id}` },
          { label: 'Progress' },
        ]}
      />

      <div className={styles.body}>
        <ProgressDashboard
          courseName={workspace.name}
          documentCount={entries.length}
          readyDocumentCount={readyCount}
          progress={progress}
          isLoading={isLoading}
          error={error}
        />
      </div>
    </div>
  );
}
