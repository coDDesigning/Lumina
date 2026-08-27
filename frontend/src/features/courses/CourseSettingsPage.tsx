import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { describeError } from '@/api/errors';
import { queryKeys } from '@/api/queryKeys';
import { settingsAPI } from '@/api/settings';
import { useAuth } from '@/context/AuthContext';
import { useCourseSettings } from './useCourseSettings';
import { queryCache } from '@/lib/query/cache';
import { EDUCATION_LEVEL_LABELS } from '@/api/types';
import type { EducationLevel } from '@/api/types';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import type { Workspace } from '@/data/workspaces';
import { Alert } from '@/ui/Alert';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { ConfirmDialog } from '@/ui/ConfirmDialog';
import { Input, Select, Textarea } from '@/ui/Input';
import { PageHeader } from '@/ui/PageHeader';
import { ErrorState } from '@/ui/ErrorState';
import { Skeleton } from '@/ui/Skeleton';
import { useToast } from '@/ui/toastContext';
import styles from './CourseSettingsPage.module.css';

export interface CourseSettingsPageProps {
  workspace: Workspace;
  onSave: (workspace: Workspace) => Promise<void> | void;
  onDelete: (courseId: string) => Promise<void>;
}

const DEFAULT_PREFERENCES = {
  studyMode: 'Exam',
  difficulty: 'Adaptive',
  questionCount: 10,
  summaryLength: 'Medium',
  detailLevel: 'Balanced',
};

function toCourseForm(workspace: Workspace) {
  return {
    name: workspace.name,
    subjectArea: workspace.subjectArea,
    educationLevel: workspace.educationLevel,
    semester: workspace.semester,
    examDate: workspace.examDate,
    topics: workspace.topics.join(', '),
    syllabus: workspace.syllabus,
  };
}

export default function CourseSettingsPage({
  workspace,
  onSave,
  onDelete,
}: CourseSettingsPageProps) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { showToast } = useToast();
  useDocumentTitle(`${workspace.name} · Settings`);

  const isSupportView = Boolean(user && workspace.ownerId != null && workspace.ownerId !== user.id);
  const ownerDisplayName =
    workspace.ownerName ||
    workspace.ownerEmail ||
    (workspace.ownerId ? `User #${workspace.ownerId}` : 'another user');

  const [course, setCourse] = useState(() => toCourseForm(workspace));
  const [isSavingCourse, setIsSavingCourse] = useState(false);
  const [courseError, setCourseError] = useState<string | null>(null);

  const [preferences, setPreferences] = useState(DEFAULT_PREFERENCES);
  const [loadedPreferences, setLoadedPreferences] = useState(DEFAULT_PREFERENCES);
  const [isSavingPreferences, setIsSavingPreferences] = useState(false);
  const [preferencesError, setPreferencesError] = useState<string | null>(null);

  const [isConfirmingDelete, setIsConfirmingDelete] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const [isArchiving, setIsArchiving] = useState(false);
  const [archiveError, setArchiveError] = useState<string | null>(null);

  const courseId = Number(workspace.id);

  const settings = useCourseSettings(courseId);
  const storedSettings = settings.status === 'success' ? settings.data : undefined;

  useEffect(() => {
    if (!storedSettings) {
      return;
    }
    const stored = {
      studyMode: storedSettings.study_mode,
      difficulty: storedSettings.difficulty,
      questionCount: storedSettings.question_count,
      summaryLength: storedSettings.summary_length,
      detailLevel: storedSettings.detail_level,
    };
    setPreferences(stored);
    setLoadedPreferences(stored);
  }, [storedSettings]);

  function updateCourse(field: keyof typeof course, value: string) {
    setCourse((current) => ({ ...current, [field]: value }));
  }

  async function saveCourse(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSavingCourse) {
      return;
    }
    setCourseError(null);
    setIsSavingCourse(true);

    try {
      await onSave({
        ...workspace,
        name: course.name.trim(),
        subjectArea: course.subjectArea.trim(),
        educationLevel: course.educationLevel,
        semester: course.semester.trim(),
        examDate: course.examDate,
        topics: course.topics
          .split(',')
          .map((topic) => topic.trim())
          .filter(Boolean),
        syllabus: course.syllabus.trim(),
        updatedAt: 'Updated just now',
      });
      showToast({ tone: 'success', title: 'Course details saved' });
    } catch (caught) {
      setCourseError(describeError(caught, "Those changes couldn't be saved.").message);
    } finally {
      setIsSavingCourse(false);
    }
  }

  async function savePreferences(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSavingPreferences) {
      return;
    }
    setPreferencesError(null);
    setIsSavingPreferences(true);

    try {
      const saved = await settingsAPI.update(courseId, {
        study_mode: preferences.studyMode,
        difficulty: preferences.difficulty,
        question_count: preferences.questionCount,
        summary_length: preferences.summaryLength,
        detail_level: preferences.detailLevel,
      });
      queryCache.setData(queryKeys.courseSettings(courseId), saved);
      setLoadedPreferences(preferences);
      showToast({ tone: 'success', title: 'Defaults saved' });
    } catch (caught) {
      setPreferencesError(describeError(caught, "Those defaults couldn't be saved.").message);
    } finally {
      setIsSavingPreferences(false);
    }
  }

  async function toggleArchive() {
    setArchiveError(null);
    setIsArchiving(true);
    const nextArchived = !workspace.isArchived;
    try {
      await onSave({
        ...workspace,
        isArchived: nextArchived,
      });
      showToast({
        tone: 'success',
        title: nextArchived ? 'Course archived' : 'Course restored',
        message: nextArchived
          ? `${workspace.name} has been archived.`
          : `${workspace.name} is now active.`,
      });
    } catch (caught) {
      setArchiveError(
        describeError(
          caught,
          nextArchived
            ? "That course couldn't be archived. Try again."
            : "That course couldn't be restored. Try again.",
        ).message,
      );
    } finally {
      setIsArchiving(false);
    }
  }

  async function confirmDelete() {
    setDeleteError(null);
    setIsDeleting(true);
    try {
      await onDelete(workspace.id);
      navigate('/dashboard', { replace: true });
    } catch (caught) {
      setDeleteError(
        describeError(caught, "That course couldn't be deleted. Try again.").message,
      );
    } finally {
      setIsDeleting(false);
    }
  }

  return (
    <div className={styles.page}>
      <PageHeader
        courseId={workspace.id}
        crumbs={[
          { label: 'Courses', to: '/dashboard' },
          { label: workspace.name, to: `/courses/${workspace.id}` },
          { label: 'Settings' },
        ]}
        badges={isSupportView ? <Badge tone="accent">Read-Only Support</Badge> : null}
      />

      <div className={styles.body}>
        <h1 className={styles.title}>Course settings</h1>
        <p className={styles.subtitle}>
          What Lumina knows about this course, and how it generates by default.
        </p>

        {isSupportView ? (
          <Alert tone="info" className={styles.spaced}>
            <strong>Read-Only Support View</strong> — Viewing settings for course owned by{' '}
            <strong>{ownerDisplayName}</strong>. Editing, archiving, and deleting are disabled.
          </Alert>
        ) : null}

        <section className={styles.section}>
          <span className={styles.sectionLabel}>Course details</span>
          <p className={styles.sectionLede}>
            Topics and the syllabus are used to focus what Lumina generates and to tag quiz
            questions, so keeping them current improves every result.
          </p>

          <form onSubmit={saveCourse}>
            {courseError ? (
              <Alert tone="destructive" live="alert" className={styles.spaced}>
                {courseError}
              </Alert>
            ) : null}

            <div className={styles.grid}>
              <Input
                label="Course name"
                fieldClassName={styles.span}
                required
                pattern=".*\S.*"
                title="Course name cannot be empty"
                value={course.name}
                onChange={(event) => updateCourse('name', event.target.value)}
                disabled={isSupportView}
              />

              <Select
                label="Education level"
                value={course.educationLevel}
                onChange={(event) => updateCourse('educationLevel', event.target.value)}
                disabled={isSupportView}
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
                value={course.subjectArea}
                onChange={(event) => updateCourse('subjectArea', event.target.value)}
                disabled={isSupportView}
              />

              <Input
                label="Term"
                optional
                value={course.semester}
                onChange={(event) => updateCourse('semester', event.target.value)}
                hint="Free text — write it however your university does."
                disabled={isSupportView}
              />

              <Input
                label="Exam date"
                optional
                type="date"
                value={course.examDate}
                onChange={(event) => updateCourse('examDate', event.target.value)}
                disabled={isSupportView}
              />

              <Input
                label="Topics"
                optional
                fieldClassName={styles.span}
                value={course.topics}
                onChange={(event) => updateCourse('topics', event.target.value)}
                placeholder="Separate topics with commas"
                disabled={isSupportView}
              />

              <Textarea
                label="Syllabus"
                optional
                fieldClassName={styles.span}
                rows={4}
                value={course.syllabus}
                onChange={(event) => updateCourse('syllabus', event.target.value)}
                disabled={isSupportView}
              />
            </div>

            {!isSupportView ? (
              <div className={styles.actions}>
                <Button
                  type="submit"
                  variant="primary"
                  isLoading={isSavingCourse}
                  loadingLabel="Saving"
                >
                  Save details
                </Button>
                <Button variant="ghost" onClick={() => setCourse(toCourseForm(workspace))}>
                  Reset details
                </Button>
              </div>
            ) : null}
          </form>
        </section>

        <section className={styles.section}>
          <span className={styles.sectionLabel}>Defaults for this course</span>
          <p className={styles.sectionLede}>
            These pre-fill the options whenever you generate something here. You can still change
            them on any single request.
          </p>

          {settings.status === 'pending' || settings.status === 'idle' ? (
            <div className={styles.loading} aria-hidden="true">
              <Skeleton variant="block" />
              <Skeleton variant="block" />
            </div>
          ) : settings.status === 'error' ? (
            <ErrorState
              title="This course's defaults could not be loaded"
              onRetry={() => {
                void settings.refetch();
              }}
            >
              {settings.error?.message}
            </ErrorState>
          ) : (
            <form onSubmit={savePreferences}>
              {preferencesError ? (
                <Alert tone="destructive" live="alert" className={styles.spaced}>
                  {preferencesError}
                </Alert>
              ) : null}

              <div className={styles.grid}>
                <Select
                  label="Study mode"
                  value={preferences.studyMode}
                  onChange={(event) =>
                    setPreferences((current) => ({ ...current, studyMode: event.target.value }))
                  }
                  hint="Exam focuses guides on what gets tested."
                  disabled={isSupportView}
                >
                  <option value="Exam">Exam focused</option>
                  <option value="General">General understanding</option>
                </Select>

                <Select
                  label="Quiz difficulty"
                  value={preferences.difficulty}
                  onChange={(event) =>
                    setPreferences((current) => ({ ...current, difficulty: event.target.value }))
                  }
                  disabled={isSupportView}
                >
                  <option value="Adaptive">Adaptive</option>
                  <option value="Easy">Easy</option>
                  <option value="Medium">Medium</option>
                  <option value="Hard">Hard</option>
                </Select>

                <Input
                  label="Questions per quiz"
                  type="number"
                  min={5}
                  max={50}
                  value={preferences.questionCount}
                  onChange={(event) =>
                    setPreferences((current) => ({
                      ...current,
                      questionCount: Number(event.target.value),
                    }))
                  }
                  hint="Between 5 and 50 here. A single quiz generates up to 20."
                  disabled={isSupportView}
                />

                <Select
                  label="Guide length"
                  value={preferences.summaryLength}
                  onChange={(event) =>
                    setPreferences((current) => ({
                      ...current,
                      summaryLength: event.target.value,
                    }))
                  }
                  disabled={isSupportView}
                >
                  <option value="Short">Short</option>
                  <option value="Medium">Medium</option>
                  <option value="Long">Long</option>
                </Select>

                <Select
                  label="Guide depth"
                  fieldClassName={styles.span}
                  value={preferences.detailLevel}
                  onChange={(event) =>
                    setPreferences((current) => ({ ...current, detailLevel: event.target.value }))
                  }
                  disabled={isSupportView}
                >
                  <option value="Concise">Concise</option>
                  <option value="Balanced">Balanced</option>
                  <option value="Detailed">Detailed</option>
                </Select>
              </div>

              {!isSupportView ? (
                <div className={styles.actions}>
                  <Button
                    type="submit"
                    variant="primary"
                    isLoading={isSavingPreferences}
                    loadingLabel="Saving"
                  >
                    Save defaults
                  </Button>
                  <Button variant="ghost" onClick={() => setPreferences(loadedPreferences)}>
                    Reset defaults
                  </Button>
                </div>
              ) : null}
            </form>
          )}
        </section>

        {!isSupportView ? (
          <>
            <section className={styles.section}>
              <h2 className={styles.sectionLabel}>
                {workspace.isArchived ? 'Restore course' : 'Archive course'}
              </h2>
              <p className={styles.sectionLede}>
                {workspace.isArchived
                  ? 'Restore this course to return it to your active courses list.'
                  : 'Archiving removes this course from your active courses list without deleting any documents, quizzes, or progress. You can restore it at any time.'}
              </p>
              {archiveError ? (
                <Alert tone="destructive" live="alert" className={styles.spaced}>
                  {archiveError}
                </Alert>
              ) : null}
              <div className={styles.actions}>
                <Button
                  variant="secondary"
                  isLoading={isArchiving}
                  loadingLabel={workspace.isArchived ? 'Restoring' : 'Archiving'}
                  onClick={toggleArchive}
                >
                  {workspace.isArchived ? 'Restore course' : 'Archive course'}
                </Button>
              </div>
            </section>

            <section className={styles.danger}>
              <h2 className={styles.dangerTitle}>Delete this course</h2>
              <p className={styles.dangerBody}>
                This removes the course and everything in it — uploaded files, generated guides and
                quizzes, attempts and progress — permanently and immediately. There is no undo and no
                recycle bin.
              </p>
              <div className={styles.dangerAction}>
                <Button
                  variant="destructive"
                  wrap
                  onClick={() => {
                    setDeleteError(null);
                    setIsConfirmingDelete(true);
                  }}
                >
                  Delete {workspace.name}
                </Button>
              </div>
            </section>
          </>
        ) : null}
      </div>

      <ConfirmDialog
        open={isConfirmingDelete}
        onClose={() => {
          setIsConfirmingDelete(false);
          setDeleteError(null);
        }}
        onConfirm={confirmDelete}
        title={`Delete ${workspace.name}?`}
        description="This permanently erases the course, everything you uploaded to it, and everything Lumina made from it. It cannot be undone."
        confirmLabel="Delete permanently"
        pendingLabel="Deleting"
        isPending={isDeleting}
        confirmPhrase={workspace.name}
        confirmPhraseLabel={`Type ${workspace.name} to confirm`}
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
