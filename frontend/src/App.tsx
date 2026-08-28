import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactElement } from 'react'
import { Navigate, Route, Routes, useParams } from 'react-router-dom'
import { toWorkspaceProgress } from './data/workspaces'
import type { Workspace, WorkspaceDraft, WorkspaceProgress } from './data/workspaces'
import CourseSettingsPage from './features/courses/CourseSettingsPage'
import CoursesPage from './features/courses/CoursesPage'
import ActivityPage from './features/activity/ActivityPage'
import ProgressPage from './features/workspace/ProgressPage'
import ExamModePage from './features/examMode/ExamModePage'
import ExamModePlanPage from './features/examMode/ExamModePlanPage'
import ExamModeComparePage from './features/examMode/ExamModeComparePage'
import ExamModeTopicPage from './features/examMode/ExamModeTopicPage'
import QuizAttemptPage from './features/study/quiz/QuizAttemptPage'
import QuizResultsPage from './features/study/quiz/QuizResultsPage'
import WorkspacePage from './features/workspace/WorkspacePage'
import AccountLayout from './features/account/AccountLayout'
import AccountYouPage from './features/account/AccountYouPage'
import AccountAppearancePage from './features/account/AccountAppearancePage'
import { AiPreferencesSection } from './features/account/AiPreferencesSection'
import { ProfileKnowledgeSection } from './features/account/ProfileKnowledgeSection'
import AdminPage from './features/admin/AdminPage'
import LoginPage from './features/auth/LoginPage'
import RegisterPage from './features/auth/RegisterPage'
import LandingPage from './features/marketing/LandingPage'
import { AppShell } from './app/AppShell'
import { ThemeProvider } from './app/ThemeProvider'
import { ToastProvider } from './ui/ToastProvider'
import { ProtectedRoute } from './components/ProtectedRoute'
import { CourseRoute } from './app/CourseRoute'
import { useAuth } from './context/AuthContext'
import { coursesAPI } from './api/courses'
import {
  afterCourseCreated,
  afterCourseDeleted,
  afterCourseUpdated,
} from './api/invalidations'
import { queryKeys } from './api/queryKeys'
import { queryCache } from './lib/query/cache'
import { useQuery } from './lib/query/useQuery'
import { progressAPI } from './api/progress'
import type { Course, CourseProgressSummary } from './api/types'

const ACTIVE_WORKSPACE_STORAGE_KEY = 'lumina.activeWorkspaceId'
const workspaceAccents: Workspace['accent'][] = [
  'blue',
  'violet',
  'rose',
  'amber',
]

type WorkspaceRouteProps = {
  workspaces: Workspace[]
  onSelect: (courseId: string) => void
  onUpdateProgress?: (courseId: string, progress: Partial<WorkspaceProgress>) => void
}

function toWorkspace(course: Course): Workspace {
  return mapCourseToWorkspace(course, 0, null)
}

function WorkspaceRoute({
  workspaces,
  onSelect,
  onUpdateProgress,
}: WorkspaceRouteProps) {
  return (
    <CourseRoute
      workspaces={workspaces}
      onSelect={onSelect}
      toWorkspace={toWorkspace}
      render={(workspace) => (
        <WorkspacePage
          key={workspace.id}
          workspace={workspace}
          onUpdateProgress={onUpdateProgress}
        />
      )}
    />
  )
}

function CourseScopedRoute({
  workspaces,
  onSelect,
  render,
}: WorkspaceRouteProps & { render: (workspace: Workspace) => ReactElement }) {
  return (
    <CourseRoute
      workspaces={workspaces}
      onSelect={onSelect}
      toWorkspace={toWorkspace}
      render={render}
    />
  )
}

type CourseSettingsRouteProps = WorkspaceRouteProps & {
  onSave: (workspace: Workspace) => Promise<void> | void
  onDelete: (courseId: string) => Promise<void>
}

function LegacyEditRedirect() {
  const { courseId } = useParams()
  return <Navigate to={`/courses/${courseId}/settings`} replace />
}

function LegacyGuideRedirect() {
  const { courseId, outputId } = useParams()
  const id = Number(outputId)
  const target = Number.isInteger(id) && id > 0
    ? `/courses/${courseId}?artifact=${id}`
    : `/courses/${courseId}`
  return <Navigate to={target} replace />
}

function LegacyWorkspaceRedirect() {
  const { courseId, '*': rest } = useParams()
  const tail = rest ? `/${rest}` : ''
  return <Navigate to={`/courses/${courseId}${tail}`} replace />
}

function CourseSettingsRoute({
  workspaces,
  onSelect,
  onSave,
  onDelete,
}: CourseSettingsRouteProps) {
  return (
    <CourseRoute
      workspaces={workspaces}
      onSelect={onSelect}
      toWorkspace={toWorkspace}
      render={(workspace) => (
        <CourseSettingsPage
          key={workspace.id}
          workspace={workspace}
          onSave={onSave}
          onDelete={onDelete}
        />
      )}
    />
  )
}

function mapCourseToWorkspace(
  course: Course,
  index: number,
  progress: WorkspaceProgress | null,
): Workspace {
  return {
    id: course.id.toString(),
    ownerId: course.owner_id,
    ownerName: course.owner_name ?? null,
    ownerEmail: course.owner_email ?? null,
    name: course.title,
    subjectArea: course.subject_area || '',
    educationLevel: course.education_level || 'unspecified',
    semester: course.semester || '',
    examDate: course.exam_date || '',
    topics: course.topics ?? [],
    syllabus: course.syllabus || '',
    progress,
    updatedAt: new Date(course.updated_at).toLocaleDateString(),
    accent: workspaceAccents[index % workspaceAccents.length],
    isArchived: course.is_archived ?? false,
  }
}

function App() {
  const { isAuthenticated } = useAuth()
  const [progressOverrides, setProgressOverrides] = useState<
    Record<string, Partial<WorkspaceProgress>>
  >({})

  const [activeWorkspaceId, setActiveWorkspaceId] = useState(
    () => localStorage.getItem(ACTIVE_WORKSPACE_STORAGE_KEY) ?? '',
  )

  const coursesQuery = useQuery<Course[]>({
    key: isAuthenticated ? queryKeys.courses() : null,
    fetcher: ({ signal }) => coursesAPI.list({ signal }),
    fallbackMessage: 'Failed to load workspaces. Please try again.',
  })

  const progressQuery = useQuery<CourseProgressSummary[]>({
    key: isAuthenticated ? queryKeys.coursesProgress() : null,
    fetcher: ({ signal }) => progressAPI.listAll({ signal }),
    fallbackMessage: 'Progress could not be loaded.',
  })

  const courses = coursesQuery.data
  const progressRows = progressQuery.data

  const workspaces = useMemo<Workspace[]>(() => {
    if (!courses) {
      return []
    }
    const summaries = progressRows
      ? new Map(progressRows.map((row) => [row.course_id, toWorkspaceProgress(row)]))
      : null
    return courses.map((course, index) => {
      const workspace = mapCourseToWorkspace(
        course,
        index,
        summaries?.get(course.id) ?? null,
      )
      const override = progressOverrides[workspace.id]
      if (!override || !workspace.progress) {
        return workspace
      }
      return { ...workspace, progress: { ...workspace.progress, ...override } }
    })
  }, [courses, progressRows, progressOverrides])

  useEffect(() => {
    setActiveWorkspaceId((current) => {
      if (!current && workspaces.length > 0) {
        return workspaces[0].id
      }
      return current
    })
  }, [workspaces])

  useEffect(() => {
    if (progressRows) {
      setProgressOverrides({})
    }
  }, [progressRows])

  const haveWorkspacesArrived =
    !isAuthenticated || coursesQuery.status === 'success' || coursesQuery.status === 'error'
  const workspacesError = coursesQuery.error?.message ?? null

  useEffect(() => {
    if (activeWorkspaceId) {
      localStorage.setItem(ACTIVE_WORKSPACE_STORAGE_KEY, activeWorkspaceId)
    }
  }, [activeWorkspaceId])

  const selectWorkspace = (courseId: string) => {
    setActiveWorkspaceId(courseId)
  }

  const createWorkspace = async (draft: WorkspaceDraft) => {
    try {
      const newCourse = await coursesAPI.create({
        title: draft.name.trim(),
        subject_area: draft.subjectArea.trim(),
        education_level: draft.educationLevel,
        syllabus: draft.syllabus.trim(),
        semester: draft.semester.trim(),
        exam_date: draft.examDate || null,
        topics: draft.topics,
      })

      const newWorkspace = mapCourseToWorkspace(newCourse, workspaces.length, {
        averageScore: null,
        timeSpentSeconds: null,
        lastActivity: null,
        status: 'no_documents',
      })
      queryCache.setData<Course[]>(queryKeys.courses(), (previous) =>
        previous ? [newCourse, ...previous] : previous,
      )
      afterCourseCreated()
      setActiveWorkspaceId(newWorkspace.id)
      return newWorkspace
    } catch (error) {
      console.error('Failed to create workspace', error)
      throw error
    }
  }

  const deleteWorkspace = async (courseId: string) => {
    await coursesAPI.delete(Number(courseId))
    queryCache.setData<Course[]>(queryKeys.courses(), (previous) =>
      previous?.filter((course) => String(course.id) !== courseId),
    )
    afterCourseDeleted(Number(courseId))
    const remaining = workspaces.filter(
      (workspace) => workspace.id !== courseId,
    )

    if (activeWorkspaceId !== courseId) return
    const nextWorkspaceId = remaining[0]?.id ?? ''
    if (!nextWorkspaceId) {
      localStorage.removeItem(ACTIVE_WORKSPACE_STORAGE_KEY)
    }
    setActiveWorkspaceId(nextWorkspaceId)
  }

  const updateWorkspace = async (updatedWorkspace: Workspace) => {
    try {
      const updatedCourse = await coursesAPI.update(
        Number(updatedWorkspace.id),
        {
          title: updatedWorkspace.name.trim(),
          subject_area: updatedWorkspace.subjectArea.trim(),
          education_level: updatedWorkspace.educationLevel,
          syllabus: updatedWorkspace.syllabus.trim(),
          semester: updatedWorkspace.semester.trim(),
          exam_date: updatedWorkspace.examDate || null,
          topics: updatedWorkspace.topics,
          is_archived: updatedWorkspace.isArchived,
        },
      )

      queryCache.setData<Course[]>(queryKeys.courses(), (previous) =>
        previous?.map((course) =>
          String(course.id) === updatedWorkspace.id ? updatedCourse : course,
        ),
      )
      afterCourseUpdated(Number(updatedWorkspace.id))
    } catch (error) {
      console.error('Failed to update workspace', error)
      throw error
    }
  }

  const updateWorkspaceProgress = useCallback(
    (courseId: string, progress: Partial<WorkspaceProgress>) => {
      setProgressOverrides((current) => ({
        ...current,
        [courseId]: { ...current[courseId], ...progress },
      }))
    },
    [],
  )

  return (
    <ThemeProvider>
      <ToastProvider>
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />

          <Route element={<ProtectedRoute />}>
            <Route element={<AppShell />}>
              <Route
                path="/dashboard"
          element={
            <CoursesPage
              workspaces={workspaces}
              isLoading={!haveWorkspacesArrived}
              error={workspacesError}
              onRetry={() => {
                void coursesQuery.refetch()
                void progressQuery.refetch()
              }}
              onCreate={createWorkspace}
              onSelect={selectWorkspace}
              onDelete={deleteWorkspace}
            />
          }
        />
        <Route path="/activity" element={<ActivityPage />} />
        <Route
          path="/courses/:courseId"
          element={
            <WorkspaceRoute
              workspaces={workspaces}
              onSelect={selectWorkspace}
              onUpdateProgress={updateWorkspaceProgress}
            />
          }
        />
        <Route
          path="/courses/:courseId/settings"
          element={
            <CourseSettingsRoute
              workspaces={workspaces}
              onSelect={selectWorkspace}
              onSave={updateWorkspace}
              onDelete={deleteWorkspace}
            />
          }
        />
        <Route
          path="/courses/:courseId/progress"
          element={
            <CourseScopedRoute
              workspaces={workspaces}
              onSelect={selectWorkspace}
              render={(workspace) => <ProgressPage key={workspace.id} workspace={workspace} />}
            />
          }
        />
        {/*
          Exam Mode is course-scoped and every persistent identity is in the
          URL, so a saved plan, a comparison, and a topic all open cold.
          Most specific first: a plan's own path must not swallow its children.
        */}
        <Route
          path="/courses/:courseId/exam-mode"
          element={
            <CourseScopedRoute
              workspaces={workspaces}
              onSelect={selectWorkspace}
              render={(workspace) => <ExamModePage key={workspace.id} workspace={workspace} />}
            />
          }
        />
        <Route
          path="/courses/:courseId/exam-mode/plans/:planId/compare/:otherPlanId"
          element={
            <CourseScopedRoute
              workspaces={workspaces}
              onSelect={selectWorkspace}
              render={(workspace) => <ExamModeComparePage workspace={workspace} />}
            />
          }
        />
        <Route
          path="/courses/:courseId/exam-mode/plans/:planId/topics/:topicKey"
          element={
            <CourseScopedRoute
              workspaces={workspaces}
              onSelect={selectWorkspace}
              render={(workspace) => <ExamModeTopicPage workspace={workspace} />}
            />
          }
        />
        <Route
          path="/courses/:courseId/exam-mode/plans/:planId"
          element={
            <CourseScopedRoute
              workspaces={workspaces}
              onSelect={selectWorkspace}
              render={(workspace) => <ExamModePlanPage workspace={workspace} />}
            />
          }
        />
        <Route
          path="/courses/:courseId/guides/:outputId"
          element={<LegacyGuideRedirect />}
        />
        <Route
          path="/courses/:courseId/practice/:quizId/attempts/:attemptId"
          element={
            <CourseScopedRoute
              workspaces={workspaces}
              onSelect={selectWorkspace}
              render={(workspace) => <QuizResultsPage workspace={workspace} />}
            />
          }
        />
        <Route
          path="/courses/:courseId/practice/:quizId"
          element={
            <CourseScopedRoute
              workspaces={workspaces}
              onSelect={selectWorkspace}
              render={(workspace) => <QuizAttemptPage workspace={workspace} />}
            />
          }
        />
        <Route path="/courses/:courseId/edit" element={<LegacyEditRedirect />} />
        <Route path="/workspaces/:courseId/*" element={<LegacyWorkspaceRedirect />} />
        <Route path="/workspaces/:courseId" element={<LegacyWorkspaceRedirect />} />
        <Route path="/profile" element={<Navigate to="/account" replace />} />
        <Route path="/settings" element={<Navigate to="/dashboard" replace />} />
        <Route path="/account" element={<AccountLayout />}>
          <Route index element={<AccountYouPage />} />
          <Route path="background" element={<ProfileKnowledgeSection />} />
          <Route path="ai" element={<AiPreferencesSection />} />
          <Route path="appearance" element={<AccountAppearancePage />} />
        </Route>
        <Route
          path="/admin"
          element={<AdminPage />}
        />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </ToastProvider>
    </ThemeProvider>
  )
}

export default App
