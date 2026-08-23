import { useCallback, useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import { Navigate, Route, Routes, useParams } from 'react-router-dom'
import type { Workspace, WorkspaceDraft } from './data/workspaces'
import CourseSettingsPage from './features/courses/CourseSettingsPage'
import CoursesPage from './features/courses/CoursesPage'
import ProgressPage from './features/workspace/ProgressPage'
import GuidePage from './features/study/GuidePage'
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
import { RouteLoading } from './app/RouteLoading'
import { useAuth } from './context/AuthContext'
import { coursesAPI } from './api/courses'
import { progressAPI } from './api/progress'
import { describeError } from './api/errors'
import type { Course, CourseProgressResponse } from './api/types'

const ACTIVE_WORKSPACE_STORAGE_KEY = 'lumina.activeWorkspaceId'
const workspaceAccents: Workspace['accent'][] = [
  'blue',
  'violet',
  'rose',
  'amber',
]

function WorkspaceLoading() {
  return <RouteLoading label="Loading course" />
}

type WorkspaceRouteProps = {
  workspaces: Workspace[]
  isLoading?: boolean
  onSelect: (courseId: string) => void
  onUpdateProgress?: (courseId: string, progress: number) => void
}

function WorkspaceRoute({ workspaces, isLoading, onSelect, onUpdateProgress }: WorkspaceRouteProps) {
  const { courseId } = useParams()
  const workspace = workspaces.find(({ id }) => id === courseId)

  useEffect(() => {
    if (workspace) onSelect(workspace.id)
  }, [onSelect, workspace])

  if (isLoading) {
    return <WorkspaceLoading />
  }

  if (!workspace) return <Navigate to="/" replace />
  return (
    <WorkspacePage
      key={workspace.id}
      workspace={workspace}
      onUpdateProgress={onUpdateProgress}
    />
  )
}

function CourseScopedRoute({
  workspaces,
  isLoading,
  onSelect,
  render,
}: WorkspaceRouteProps & { render: (workspace: Workspace) => ReactElement }) {
  const { courseId } = useParams()
  const workspace = workspaces.find(({ id }) => id === courseId)

  useEffect(() => {
    if (workspace) onSelect(workspace.id)
  }, [onSelect, workspace])

  if (isLoading) return <WorkspaceLoading />
  if (!workspace) return <Navigate to="/" replace />
  return render(workspace)
}

function ProgressRoute({ workspaces, isLoading, onSelect }: WorkspaceRouteProps) {
  const { courseId } = useParams()
  const workspace = workspaces.find(({ id }) => id === courseId)

  useEffect(() => {
    if (workspace) onSelect(workspace.id)
  }, [onSelect, workspace])

  if (isLoading) return <WorkspaceLoading />
  if (!workspace) return <Navigate to="/" replace />
  return <ProgressPage key={workspace.id} workspace={workspace} />
}

type CourseSettingsRouteProps = WorkspaceRouteProps & {
  onSave: (workspace: Workspace) => Promise<void> | void
  onDelete: (courseId: string) => Promise<void>
}

function LegacyEditRedirect() {
  const { courseId } = useParams()
  return <Navigate to={`/courses/${courseId}/settings`} replace />
}

function LegacyWorkspaceRedirect() {
  const { courseId, '*': rest } = useParams()
  const tail = rest ? `/${rest}` : ''
  return <Navigate to={`/courses/${courseId}${tail}`} replace />
}

function CourseSettingsRoute({
  workspaces,
  isLoading,
  onSelect,
  onSave,
  onDelete,
}: CourseSettingsRouteProps) {
  const { courseId } = useParams()
  const workspace = workspaces.find(({ id }) => id === courseId)

  useEffect(() => {
    if (workspace) onSelect(workspace.id)
  }, [onSelect, workspace])

  if (isLoading) {
    return <WorkspaceLoading />
  }

  if (!workspace) return <Navigate to="/" replace />
  return (
    <CourseSettingsPage
      key={workspace.id}
      workspace={workspace}
      onSave={onSave}
      onDelete={onDelete}
    />
  )
}

function mapCourseToWorkspace(
  course: Course,
  index: number,
  progressData?: CourseProgressResponse | null,
): Workspace {
  let progress: number | null = null
  let status = 'Not started'

  if (progressData) {
    if (progressData.average_score != null) {
      progress = Math.round(
        progressData.average_score <= 1
          ? progressData.average_score * 100
          : progressData.average_score,
      )
    } else if (progressData.completion != null && progressData.attempts_count > 0) {
      progress = Math.round(progressData.completion)
    }

    if (progressData.attempts_count > 0) {
      status = (progress ?? 0) >= 80 ? 'Mastered' : 'In progress'
    }
  }

  return {
    id: course.id.toString(),
    ownerId: course.owner_id,
    name: course.title,
    subjectArea: course.subject_area || '',
    educationLevel: course.education_level || 'unspecified',
    semester: course.semester || '',
    examDate: course.exam_date || '',
    topics: course.topics
      ? course.topics.split(',').map((t) => t.trim()).filter(Boolean)
      : [],
    syllabus: course.syllabus || '',
    progress,
    status,
    updatedAt: new Date(course.updated_at).toLocaleDateString(),
    accent: workspaceAccents[index % workspaceAccents.length],
  }
}

function App() {
  const { isAuthenticated } = useAuth()
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [haveWorkspacesArrived, setHaveWorkspacesArrived] = useState(false)
  const [workspacesError, setWorkspacesError] = useState<string | null>(null)

  const [activeWorkspaceId, setActiveWorkspaceId] = useState(
    () => localStorage.getItem(ACTIVE_WORKSPACE_STORAGE_KEY) ?? '',
  )

  const fetchWorkspaces = useCallback(
    async (signal?: AbortSignal) => {
      if (!isAuthenticated) {
        setWorkspacesError(null)
        return
      }
      setHaveWorkspacesArrived(false)
      setWorkspacesError(null)
      try {
        const courses = await coursesAPI.list({ signal })
        const progressResults = await Promise.allSettled(
          courses.map((course) => progressAPI.get(course.id, { signal })),
        )
        const mappedWorkspaces = courses.map((course, index) => {
          const progResult = progressResults[index]
          const progData =
            progResult?.status === 'fulfilled' ? progResult.value : null
          return mapCourseToWorkspace(course, index, progData)
        })
        setWorkspaces(mappedWorkspaces)

        setActiveWorkspaceId((current) => {
          if (mappedWorkspaces.length > 0 && !current) {
            return mappedWorkspaces[0].id
          }
          return current
        })
      } catch (error: unknown) {
        if (error instanceof Error && error.name === 'AbortError') {
          return
        }
        const described = describeError(
          error,
          'Failed to load workspaces. Please try again.',
        )
        setWorkspacesError(described.message)
      } finally {
        setHaveWorkspacesArrived(true)
      }
    },
    [isAuthenticated],
  )

  useEffect(() => {
    const controller = new AbortController()
    fetchWorkspaces(controller.signal)
    return () => {
      controller.abort()
    }
  }, [fetchWorkspaces])

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
        exam_date: draft.examDate,
        topics: draft.topics,
      })

      const newWorkspace = mapCourseToWorkspace(newCourse, workspaces.length)
      setWorkspaces((current) => [newWorkspace, ...current])
      setActiveWorkspaceId(newWorkspace.id)
      return newWorkspace
    } catch (error) {
      console.error('Failed to create workspace', error)
      throw error
    }
  }

  const deleteWorkspace = async (courseId: string) => {
    await coursesAPI.delete(Number(courseId))
    const remaining = workspaces.filter(
      (workspace) => workspace.id !== courseId,
    )
    setWorkspaces(remaining)

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
          exam_date: updatedWorkspace.examDate,
          topics: updatedWorkspace.topics.join(', '),
        },
      )

      const updatedMappedWorkspace = mapCourseToWorkspace(
        updatedCourse,
        workspaces.findIndex((w) => w.id === updatedWorkspace.id),
      )
      setWorkspaces((current) =>
        current.map((workspace) =>
          workspace.id === updatedWorkspace.id
            ? updatedMappedWorkspace
            : workspace,
        ),
      )
    } catch (error) {
      console.error('Failed to update workspace', error)
    }
  }

  const updateWorkspaceProgress = useCallback(
    (courseId: string, progress: number) => {
      setWorkspaces((current) => {
        const target = current.find((w) => w.id === courseId)
        if (!target || target.progress === progress) return current
        return current.map((w) =>
          w.id === courseId ? { ...w, progress } : w,
        )
      })
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
              onRetry={() => fetchWorkspaces()}
              onCreate={createWorkspace}
              onSelect={selectWorkspace}
              onDelete={deleteWorkspace}
            />
          }
        />
        <Route
          path="/courses/:courseId"
          element={
            <WorkspaceRoute
              workspaces={workspaces}
              isLoading={!haveWorkspacesArrived}
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
              isLoading={!haveWorkspacesArrived}
              onSelect={selectWorkspace}
              onSave={updateWorkspace}
              onDelete={deleteWorkspace}
            />
          }
        />
        <Route
          path="/courses/:courseId/progress"
          element={
            <ProgressRoute
              workspaces={workspaces}
              isLoading={!haveWorkspacesArrived}
              onSelect={selectWorkspace}
            />
          }
        />
        <Route
          path="/courses/:courseId/guides/:outputId"
          element={
            <CourseScopedRoute
              workspaces={workspaces}
              isLoading={!haveWorkspacesArrived}
              onSelect={selectWorkspace}
              render={(workspace) => <GuidePage workspace={workspace} />}
            />
          }
        />
        <Route
          path="/courses/:courseId/practice/:quizId/attempts/:attemptId"
          element={
            <CourseScopedRoute
              workspaces={workspaces}
              isLoading={!haveWorkspacesArrived}
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
              isLoading={!haveWorkspacesArrived}
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
