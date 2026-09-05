import type { Prerequisite, PrerequisiteKind } from './examPrerequisites';
import { displayFileName } from '@/components/documents/documentLabels';
import { Alert } from '@/ui/Alert';
import { LinkButton } from '@/ui/LinkButton';
import styles from './ExamPrerequisiteNotices.module.css';

const sourceNames = (documents: Prerequisite['documents']): string =>
  documents.map((entry) => displayFileName(entry.label)).join(', ');

interface Copy {
  title: string;
  body: (documents: Prerequisite['documents']) => string;
  /** Where the student goes to fix it, relative to the course. */
  action?: { label: string; to: (courseId: number) => string };
}

/**
 * Every prerequisite says what is true, and what to do about it. "Nothing here"
 * is not enough when the application already knows the next step.
 */
const COPY: Record<PrerequisiteKind, Copy> = {
  exam_date_missing: {
    title: 'This course has no exam date',
    body: () =>
      'A first plan needs a date to rank against. Everything else here works without one.',
    action: { label: 'Set an exam date', to: (id) => `/courses/${id}/settings` },
  },
  exam_date_passed: {
    title: 'The exam date has passed',
    body: () =>
      'Everything you already have stays readable. Set the next date to build a new plan.',
    action: { label: 'Update the exam date', to: (id) => `/courses/${id}/settings` },
  },
  no_sources: {
    title: 'No source is ready to read',
    body: () => 'Exam Mode reads the material you have already uploaded to this course.',
    action: { label: 'Add sources', to: (id) => `/courses/${id}` },
  },
  sources_processing: {
    title: 'Some sources are still being read',
    body: (documents) =>
      `${sourceNames(documents)} ${
        documents.length === 1 ? 'is' : 'are'
      } not ready yet. You can analyse what is ready now and scan again later.`,
  },
  source_failed: {
    title: 'Some sources could not be read',
    body: (documents) =>
      `${sourceNames(documents)} failed to process, so ${
        documents.length === 1 ? 'it is' : 'they are'
      } not counted as material. Retry or replace ${
        documents.length === 1 ? 'it' : 'them'
      } from the course.`,
    action: { label: 'Review your sources', to: (id) => `/courses/${id}` },
  },
  material_not_indexed: {
    title: 'Your material is not searchable yet',
    body: () =>
      'Your sources are ready but have not been indexed. This one is on our side — uploading them again will not help.',
  },
  no_syllabus: {
    title: 'This course has no syllabus',
    body: () =>
      'Topics can still be discovered from the material you select, but a syllabus is the strongest signal of what is examinable.',
    action: { label: 'Add a syllabus', to: (id) => `/courses/${id}/settings` },
  },
  no_course_topics: {
    title: 'This course declares no topics',
    body: () =>
      'Topics can still be discovered from your syllabus and selected material. Declaring them sharpens the ranking.',
    action: { label: 'Add topics', to: (id) => `/courses/${id}/settings` },
  },
};

export interface ExamPrerequisiteNoticesProps {
  courseId: number;
  blockers: Prerequisite[];
  warnings: Prerequisite[];
  /** Support readers see the facts but never the fix-it links. */
  readOnly?: boolean;
}

function Notice({
  courseId,
  prerequisite,
  tone,
  readOnly,
}: {
  courseId: number;
  prerequisite: Prerequisite;
  tone: 'warning' | 'info';
  readOnly?: boolean;
}) {
  const copy = COPY[prerequisite.kind];
  const action =
    copy.action && !readOnly ? (
      <LinkButton variant="secondary" size="sm" to={copy.action.to(courseId)}>
        {copy.action.label}
      </LinkButton>
    ) : null;

  return (
    <Alert tone={tone} title={copy.title} actions={action}>
      {copy.body(prerequisite.documents)}
    </Alert>
  );
}

export function ExamPrerequisiteNotices({
  courseId,
  blockers,
  warnings,
  readOnly,
}: ExamPrerequisiteNoticesProps) {
  if (blockers.length === 0 && warnings.length === 0) {
    return null;
  }

  return (
    <div className={styles.notices}>
      {blockers.map((prerequisite) => (
        <Notice
          key={prerequisite.kind}
          courseId={courseId}
          prerequisite={prerequisite}
          tone="warning"
          readOnly={readOnly}
        />
      ))}
      {warnings.map((prerequisite) => (
        <Notice
          key={prerequisite.kind}
          courseId={courseId}
          prerequisite={prerequisite}
          tone="info"
          readOnly={readOnly}
        />
      ))}
    </div>
  );
}
