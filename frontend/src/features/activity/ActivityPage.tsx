import { useDocumentTitle } from '@/app/useDocumentTitle';
import { PageHeader } from '@/ui/PageHeader';
import { RecentActivity } from './RecentActivity';
import styles from './ActivityPage.module.css';

const FULL_HISTORY = 50;

export default function ActivityPage() {
  useDocumentTitle('Activity');

  return (
    <div className={styles.page}>
      <PageHeader crumbs={[{ label: 'Courses', to: '/dashboard' }, { label: 'Activity' }]} />

      <div className={styles.body}>
        <div className={styles.intro}>
          <h1 className={styles.title}>What you have been studying</h1>
          <p className={styles.subtitle}>
            Everything you have generated and every quiz you have taken, newest first, across
            all of your courses.
          </p>
        </div>

        <RecentActivity limit={FULL_HISTORY} heading="All activity" />
      </div>
    </div>
  );
}
