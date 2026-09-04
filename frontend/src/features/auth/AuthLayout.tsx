import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { Brandmark } from '@/ui/Brandmark';
import { CourseLight } from '@/ui/CourseLight';
import styles from './AuthLayout.module.css';

export interface AuthLayoutProps {
  tone: number;
  documentTitle: string;
  title: string;
  /** Omitted while a screen is still learning what it should say. */
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
  note?: ReactNode;
}

export function AuthLayout({
  tone,
  documentTitle,
  title,
  subtitle,
  children,
  footer,
  note,
}: AuthLayoutProps) {
  useDocumentTitle(documentTitle);

  return (
    <CourseLight courseId={tone} className={styles.shell}>
      <main className={styles.panel}>
        <Link to="/" className={styles.back}>
          &larr; Back to Lumina
        </Link>
        <Brandmark size="lg" />
        <h1 className={styles.title}>{title}</h1>
        {subtitle ? <p className={styles.subtitle}>{subtitle}</p> : null}
        {children}
        {footer ? <p className={styles.footer}>{footer}</p> : null}
        {note ? <p className={styles.note}>{note}</p> : null}
      </main>
    </CourseLight>
  );
}
