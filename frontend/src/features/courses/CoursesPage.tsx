import { useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import { FolderOpen, Plus, RefreshCw, Settings2, Trash2 } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { describeError } from '@/api/errors';
import { EDUCATION_LEVEL_LABELS } from '@/api/types';
import type { EducationLevel } from '@/api/types';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import type { Workspace, WorkspaceDraft } from '@/data/workspaces';
import { Alert } from '@/ui/Alert';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { Card } from '@/ui/Card';
import { ConfirmDialog } from '@/ui/ConfirmDialog';
import { CourseChip, CourseLight } from '@/ui/CourseLight';
import { Dialog } from '@/ui/Dialog';
import { EmptyState } from '@/ui/EmptyState';
import { IconButton } from '@/ui/IconButton';
import { Input, Select, Textarea } from '@/ui/Input';
import { PageHeader } from '@/ui/PageHeader';
import { Skeleton } from '@/ui/Skeleton';
import styles from './CoursesPage.module.css';

export interface CoursesPageProps {
  workspaces: Workspace[];
  isLoading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  onCreate: (draft: WorkspaceDraft) => Promise<Workspace>;
  onSelect: (courseId: string) => void;
  onDelete: (courseId: string) => Promise<void>;
}

const emptyDraft: WorkspaceDraft = {
  name: '',
  subjectArea: '',
  educationLevel: 'unspecified',
  semester: '',
  examDate: '',
  topics: '',
  syllabus: '',
};

const DAY_MS = 24 * 60 * 60 * 1000;

function examTimestamp(date: string): number | null {
  if (!date) {
    return null;
  }
  const parsed = Date.parse(`${date}T00:00:00Z`);
  return Number.isNaN(parsed) ? null : parsed;
}

function formatExamDate(date: string): string {
  const timestamp = examTimestamp(date);
  if (timestamp === null) {
    return 'No exam date';
  }
  return new Intl.DateTimeFormat('en', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(timestamp);
}

function daysUntilExam(date: string, now: number): number | null {
  const timestamp = examTimestamp(date);
  if (timestamp === null) {
    return null;
  }
  const today = Math.floor(now / DAY_MS) * DAY_MS;
  return Math.round((timestamp - today) / DAY_MS);
}

function examUrgency(days: number | null): 'destructive' | 'warning' | 'neutral' {
  if (days === null || days < 0) {
    return 'neutral';
  }
  if (days <= 14) {
    return 'destructive';
  }
  if (days <= 30) {
    return 'warning';
  }
  return 'neutral';
}

function countdownPhrase(days: number): string {
  if (days === 0) {
    return 'today';
  }
  if (days === 1) {
    return 'tomorrow';
  }
  return `in ${days} days`;
}

function examLabel(days: number | null, examDate: string): string {
  if (days === null) {
    return 'No exam date';
  }
  if (days < 0) {
    return `Exam was ${formatExamDate(examDate)}`;
  }
  if (days === 0) {
    return 'Exam today';
  }
  if (days === 1) {
    return 'Exam tomorrow';
  }
  return `Exam in ${days} days`;
}

export default function CoursesPage({
  workspaces,
  isLoading = false,
  error = null,
  onRetry,
  onCreate,
  onSelect,
  onDelete,
}: CoursesPageProps) {
  const navigate = useNavigate();
  useDocumentTitle('Courses');

  const [query, setQuery] = useState('');
  const [isCreating, setIsCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [draft, setDraft] = useState(emptyDraft);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const now = useMemo(() => Date.now(), []);

  const sorted = useMemo(() => {
    return [...workspaces].sort((left, right) => {
      const leftDays = daysUntilExam(left.examDate, now);
      const rightDays = daysUntilExam(right.examDate, now);
      const leftUpcoming = leftDays !== null && leftDays >= 0;
      const rightUpcoming = rightDays !== null && rightDays >= 0;

      if (leftUpcoming && rightUpcoming) {
        return leftDays - rightDays;
      }
      if (leftUpcoming !== rightUpcoming) {
        return leftUpcoming ? -1 : 1;
      }
      return 0;
    });
  }, [workspaces, now]);

  const normalizedQuery = query.trim().toLowerCase();
  const filtered = sorted.filter((workspace) =>
    [workspace.name, workspace.semester, ...workspace.topics]
      .join(' ')
      .toLowerCase()
      .includes(normalizedQuery),
  );

  const nextExam = sorted.find((workspace) => {
    const days = daysUntilExam(workspace.examDate, now);
    return days !== null && days >= 0;
  });
  const nextExamDays = nextExam ? daysUntilExam(nextExam.examDate, now) : null;

  const confirming = workspaces.find((workspace) => workspace.id === confirmingId) ?? null;

  useEffect(() => {
    if (!isCreating) {
      setCreateError(null);
    }
  }, [isCreating]);

  function updateDraft(field: keyof WorkspaceDraft, value: string) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreateError(null);
    setIsSubmitting(true);

    try {
      const workspace = await onCreate({ ...draft, name: draft.name.trim() });
      setDraft(emptyDraft);
      setIsCreating(false);
      navigate(`/courses/${workspace.id}`);
    } catch (caught) {
      setCreateError(
        describeError(caught, "That course couldn't be created. Try again.").message,
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleDelete() {
    if (!confirming) {
      return;
    }
    setDeleteError(null);
    setDeletingId(confirming.id);

    try {
      await onDelete(confirming.id);
      setConfirmingId(null);
    } catch (caught) {
      setDeleteError(
        describeError(caught, "That course couldn't be deleted. Try again.").message,
      );
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className={styles.page}>
      <PageHeader crumbs={[{ label: 'Courses' }]} />

      <div className={styles.body}>
        <div className={styles.intro}>
          <div>
            <h1 className={styles.title}>Your courses</h1>
            <p className={styles.subtitle}>
              {nextExam && nextExamDays !== null
                ? `Your nearest exam is ${nextExam.name}, ${countdownPhrase(nextExamDays)}.`
                : 'Pick a course to carry on, or start a new one.'}
            </p>
          </div>

          <div className={styles.toolbar}>
            <Input
              label="Search courses"
              hideLabel
              type="search"
              className={styles.search}
              fieldClassName={styles.search}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search by course, term, or topic"
            />
            <Button
              variant="primary"
              icon={<Plus aria-hidden="true" />}
              onClick={() => setIsCreating(true)}
            >
              New course
            </Button>
          </div>
        </div>

        {error ? (
          <Alert
            tone="destructive"
            live="alert"
            title="We couldn't load your courses"
            className={styles.spaced}
            actions={
              onRetry ? (
                <Button
                  variant="secondary"
                  size="sm"
                  icon={<RefreshCw aria-hidden="true" />}
                  onClick={onRetry}
                >
                  Try again
                </Button>
              ) : undefined
            }
          >
            {error}
          </Alert>
        ) : isLoading ? (
          <>
            <p className={styles.count} role="status">
              Loading your courses…
            </p>
            <div className={styles.skeletonGrid} aria-hidden="true">
              {[0, 1, 2].map((key) => (
                <Card key={key} className={styles.skeletonCard}>
                  <Skeleton width="40%" />
                  <Skeleton variant="heading" width="72%" />
                  <Skeleton width="90%" />
                  <Skeleton width="55%" />
                </Card>
              ))}
            </div>
          </>
        ) : filtered.length > 0 ? (
          <>
            <p className={styles.count}>
              {filtered.length} {filtered.length === 1 ? 'course' : 'courses'}
            </p>
            <ul className={styles.grid}>
              {filtered.map((workspace) => {
                const days = daysUntilExam(workspace.examDate, now);
                const eyebrow = workspace.subjectArea || workspace.semester || 'Course';

                return (
                  <li key={workspace.id} className={styles.cardShell}>
                    <Link
                      to={`/courses/${workspace.id}`}
                      className={styles.card}
                      onClick={() => onSelect(workspace.id)}
                    >
                      <Card elevation="raised" className={styles.card}>
                        <CourseLight courseId={workspace.id} className={styles.cardInner}>
                          <div className={styles.cardTop}>
                            <span className={styles.eyebrow}>
                              <CourseChip courseId={workspace.id} />
                              <span className={styles.eyebrowText}>{eyebrow}</span>
                            </span>
                          </div>

                          <h2 className={styles.cardTitle}>{workspace.name}</h2>

                          <p className={styles.topics}>
                            {workspace.topics.length > 0
                              ? workspace.topics.join(', ')
                              : 'No topics added yet'}
                          </p>

                          <div className={styles.meta}>
                            <Badge tone={examUrgency(days)}>{examLabel(days, workspace.examDate)}</Badge>
                            <span className="tabular">
                              {workspace.progress !== null ? (
                                <>
                                  <strong>{workspace.progress}%</strong> average score
                                </>
                              ) : (
                                'No quiz activity yet'
                              )}
                            </span>
                          </div>

                          {workspace.progress !== null ? (
                            <span className={styles.progressTrack} aria-hidden="true">
                              <span
                                className={styles.progressFill}
                                style={{ width: `${workspace.progress}%` }}
                              />
                            </span>
                          ) : null}
                        </CourseLight>
                      </Card>
                    </Link>

                    <span className={styles.cardActions}>
                      <IconButton
                        label={`Settings for ${workspace.name}`}
                        size="sm"
                        icon={<Settings2 aria-hidden="true" />}
                        onClick={() => navigate(`/courses/${workspace.id}/settings`)}
                      />
                      <IconButton
                        label={`Delete ${workspace.name}`}
                        tone="destructive"
                        size="sm"
                        icon={<Trash2 aria-hidden="true" />}
                        onClick={() => {
                          setDeleteError(null);
                          setConfirmingId(workspace.id);
                        }}
                      />
                    </span>
                  </li>
                );
              })}
            </ul>
          </>
        ) : workspaces.length > 0 ? (
          <EmptyState
            icon={<FolderOpen aria-hidden="true" />}
            title="No courses found"
            description="Try a different course name, term, or topic."
            actions={
              <Button variant="secondary" onClick={() => setQuery('')}>
                Clear search
              </Button>
            }
          />
        ) : (
          <EmptyState
            icon={<FolderOpen aria-hidden="true" />}
            title="Start with one course."
            description="A course is where your material lives. Upload a lecture, a set of notes or a past paper, and Lumina works from that."
            actions={
              <Button variant="primary" onClick={() => setIsCreating(true)}>
                Create your first course
              </Button>
            }
          />
        )}
      </div>

      <Dialog
        open={isCreating}
        onClose={() => setIsCreating(false)}
        title="New course"
        description="Only a name is required. Everything else sharpens what Lumina generates, and can be added later."
        size="lg"
        spreadFooter
        footer={
          <>
            <Button variant="ghost" onClick={() => setIsCreating(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              type="submit"
              form="create-course-form"
              isLoading={isSubmitting}
              loadingLabel="Creating"
            >
              Create course
            </Button>
          </>
        }
      >
        <form id="create-course-form" className={styles.formGrid} onSubmit={handleCreate}>
          {createError ? (
            <div className={styles.formSpan}>
              <Alert tone="destructive" live="alert">
                {createError}
              </Alert>
            </div>
          ) : null}

          <Input
            label="Course name"
            fieldClassName={styles.formSpan}
            autoFocus
            required
            pattern=".*\S.*"
            title="Course name cannot be empty"
            value={draft.name}
            onChange={(event) => updateDraft('name', event.target.value)}
            placeholder="e.g. Computer Architecture"
          />

          <Select
            label="Education level"
            optional
            value={draft.educationLevel}
            onChange={(event) => updateDraft('educationLevel', event.target.value)}
          >
            {(Object.keys(EDUCATION_LEVEL_LABELS) as EducationLevel[]).map((level) => (
              <option key={level} value={level}>
                {EDUCATION_LEVEL_LABELS[level]}
              </option>
            ))}
          </Select>

          <Input
            label="Subject area"
            optional
            value={draft.subjectArea}
            onChange={(event) => updateDraft('subjectArea', event.target.value)}
            placeholder="e.g. Computer Engineering"
          />

          <Input
            label="Term"
            optional
            value={draft.semester}
            onChange={(event) => updateDraft('semester', event.target.value)}
            placeholder="e.g. Fall 2026"
            hint="Free text — write it however your university does."
          />

          <Input
            label="Exam date"
            optional
            type="date"
            value={draft.examDate}
            onChange={(event) => updateDraft('examDate', event.target.value)}
          />

          <Input
            label="Topics"
            optional
            fieldClassName={styles.formSpan}
            value={draft.topics}
            onChange={(event) => updateDraft('topics', event.target.value)}
            placeholder="Separate topics with commas"
            hint="Used to focus what Lumina generates and to tag quiz questions."
          />

          <Textarea
            label="Syllabus"
            optional
            fieldClassName={styles.formSpan}
            rows={3}
            value={draft.syllabus}
            onChange={(event) => updateDraft('syllabus', event.target.value)}
            placeholder="Paste the course description or learning goals"
          />
        </form>
      </Dialog>

      <ConfirmDialog
        open={confirming !== null}
        onClose={() => {
          setConfirmingId(null);
          setDeleteError(null);
        }}
        onConfirm={handleDelete}
        title={confirming ? `Delete ${confirming.name}?` : 'Delete course?'}
        description={
          confirming
            ? 'This permanently erases the course, everything you uploaded to it, and everything Lumina made from it. It cannot be undone.'
            : undefined
        }
        confirmLabel="Delete permanently"
        pendingLabel="Deleting"
        isPending={deletingId !== null}
        confirmPhrase={confirming?.name}
        confirmPhraseLabel={confirming ? `Type ${confirming.name} to confirm` : undefined}
      >
        {deleteError ? (
          <Alert tone="destructive" live="alert" className={styles.spaced}>
            {deleteError}
          </Alert>
        ) : null}
      </ConfirmDialog>
    </div>
  );
}
