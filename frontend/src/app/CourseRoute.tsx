import type { ReactElement } from 'react';
import { useParams } from 'react-router-dom';
import { FolderX } from 'lucide-react';
import type { Course } from '@/api/types';
import type { Workspace } from '@/data/workspaces';
import { EmptyState } from '@/ui/EmptyState';
import { ErrorState } from '@/ui/ErrorState';
import { LinkButton } from '@/ui/LinkButton';
import { RouteLoading } from './RouteLoading';
import { useCourseRoute } from './useCourseRoute';
import styles from './CourseRoute.module.css';

export interface CourseRouteProps {
  workspaces: Workspace[];
  onSelect?: (courseId: string) => void;
  toWorkspace: (course: Course) => Workspace;
  render: (workspace: Workspace) => ReactElement;
}

export function CourseRoute({ workspaces, onSelect, toWorkspace, render }: CourseRouteProps) {
  const { courseId } = useParams();
  const { workspace, isLoading, isNotFound, error, retry } = useCourseRoute(
    courseId,
    workspaces,
    toWorkspace,
    onSelect,
  );

  if (isLoading) {
    return <RouteLoading label="Loading course" />;
  }

  if (isNotFound) {
    return (
      <div className={styles.wrap}>
        <EmptyState
          headingLevel="h1"
          icon={<FolderX aria-hidden="true" />}
          title="This course is not available"
          description="It may no longer exist, or it may not be one of yours."
          actions={<LinkButton to="/dashboard">Back to your courses</LinkButton>}
        />
      </div>
    );
  }

  if (error || !workspace) {
    return (
      <div className={styles.wrap}>
        <ErrorState
          title="This course could not be loaded"
          onRetry={retry}
          actions={
            <LinkButton variant="secondary" size="sm" to="/dashboard">
              Back to your courses
            </LinkButton>
          }
        >
          {error ?? 'Course could not be loaded.'}
        </ErrorState>
      </div>
    );
  }

  return render(workspace);
}
