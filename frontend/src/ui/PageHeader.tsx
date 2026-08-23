import { Fragment } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { cx } from '@/lib/cx';
import { CourseChip, CourseLight } from './CourseLight';
import styles from './PageHeader.module.css';

export interface Crumb {
  label: string;
  to?: string;
}

export interface PageHeaderProps {
  crumbs: Crumb[];
  badges?: ReactNode;
  actions?: ReactNode;
  courseId?: number | string;
  className?: string;
}

export function PageHeader({ crumbs, badges, actions, courseId, className }: PageHeaderProps) {
  const content = (
    <div className={cx(styles.header, className)}>
      {courseId !== undefined ? <CourseChip courseId={courseId} /> : null}

      <nav className={styles.crumbs} aria-label="Breadcrumb">
        {crumbs.map((crumb, index) => {
          const isLast = index === crumbs.length - 1;
          return (
            <Fragment key={`${crumb.label}-${index}`}>
              {index > 0 ? (
                <span className={styles.separator} aria-hidden="true">
                  /
                </span>
              ) : null}
              {crumb.to && !isLast ? (
                <Link to={crumb.to}>{crumb.label}</Link>
              ) : (
                <span className={styles.current} aria-current={isLast ? 'page' : undefined}>
                  {crumb.label}
                </span>
              )}
            </Fragment>
          );
        })}
      </nav>

      {badges ? <div className={styles.badges}>{badges}</div> : null}
      <span className={styles.spacer} />
      {actions ? <div className={styles.actions}>{actions}</div> : null}
    </div>
  );

  if (courseId === undefined) {
    return content;
  }

  return <CourseLight courseId={courseId}>{content}</CourseLight>;
}
