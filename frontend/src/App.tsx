import { useCallback, useEffect, useState } from 'react'
import { Navigate, Route, Routes, useParams } from 'react-router-dom'
import type { Workspace, WorkspaceDraft } from './data/workspaces'
import CourseSettingsPage from './features/courses/CourseSettingsPage'
import CoursesPage from './features/courses/CoursesPage'
import ProgressPage from './features/workspace/ProgressPage'
import WorkspacePage from './features/workspace/WorkspacePage'
import AccountPage from './features/account/AccountPage'
import AdminPage from './pages/AdminPage'
import LoginPage from './features/auth/LoginPage'
import RegisterPage from './features/auth/RegisterPage'
import LandingPage from './features/marketing/LandingPage'
import { AppShell } from './app/AppShell'
import { ThemeProvider } from './app/ThemeProvider'
import { ToastProvider } from './ui/ToastProvider'
import { ProtectedRoute } from './components/ProtectedRoute'
import { LoadingSpinner } from './components/LoadingSpinner'
import { useAuth } from './context/AuthContext'
import { coursesAPI } from './api/courses'
import { progressAPI } from './api/progress'
import { describeError } from './api/errors'
import type { Course, CourseProgressResponse } from './api/types'
import './App.css'
import './pages/pages.css'
import './pages/workspaces.css'


const ACTIVE_WORKSPACE_STORAGE_KEY = 'lumina.activeWorkspaceId'
const workspaceAccents: Workspace['accent'][] = [
  'blue',
  'violet',
  'rose',
  'amber',
]

function WorkspaceLoading() {
  return (
    <div className="route-loading" role="status">
      <LoadingSpinner size="lg" />
      <span className="visually-hidden">Loading course</span>
    </div>
  )
}

type WorkspaceRouteProps = {
  workspaces: Workspace[]
  isLoading?: boolean
  onSelect: (workspaceId: string) => void
  onUpdateProgress?: (workspaceId: string, progress: number) => void
}

function WorkspaceRoute({ workspaces, isLoading, onSelect, onUpdateProgress }: WorkspaceRouteProps) {
  const { workspaceId } = useParams()
  const workspace = workspaces.find(({ id }) => id === workspaceId)

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

function ProgressRoute({ workspaces, isLoading, onSelect }: WorkspaceRouteProps) {
  const { workspaceId } = useParams()
  const workspace = workspaces.find(({ id }) => id === workspaceId)

  useEffect(() => {
    if (workspace) onSelect(workspace.id)
  }, [onSelect, workspace])

  if (isLoading) return <WorkspaceLoading />
  if (!workspace) return <Navigate to="/" replace />
  return <ProgressPage key={workspace.id} workspace={workspace} />
}

type CourseSettingsRouteProps = WorkspaceRouteProps & {
  onSave: (workspace: Workspace) => Promise<void> | void
  onDelete: (workspaceId: string) => Promise<void>
}

function LegacyEditRedirect() {
  const { workspaceId } = useParams()
  return <Navigate to={`/workspaces/${workspaceId}/settings`} replace />
}

function CourseSettingsRoute({
  workspaces,
  isLoading,
  onSelect,
  onSave,
  onDelete,
}: CourseSettingsRouteProps) {
  const { workspaceId } = useParams()
  const workspace = workspaces.find(({ id }) => id === workspaceId)

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
    topics: course.topics ? course.topics.split(',').map((t) => t.trim()) : [],
    syllabus: course.syllabus || '',
    progress,
    status,
    updatedAt: new Date(course.updated_at).toLocaleDateString(),
    accent: workspaceAccents[index % workspaceAccents.length],
    sources: [],
  }
}

function App() {
  const { isAuthenticated } = useAuth()
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [isLoadingWorkspaces, setIsLoadingWorkspaces] = useState(true)
  const [workspacesError, setWorkspacesError] = useState<string | null>(null)

  const [activeWorkspaceId, setActiveWorkspaceId] = useState(
    () => localStorage.getItem(ACTIVE_WORKSPACE_STORAGE_KEY) ?? '',
  )

  const fetchWorkspaces = useCallback(
    async (signal?: AbortSignal) => {
      if (!isAuthenticated) {
        setIsLoadingWorkspaces(false)
        setWorkspacesError(null)
        return
      }
      setIsLoadingWorkspaces(true)
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
          // Abort/unmount behavior is intentionally NOT shown as an error
          return
        }
        const described = describeError(
          error,
          'Failed to load workspaces. Please try again.',
        )
        setWorkspacesError(described.message)
      } finally {
        setIsLoadingWorkspaces(false)
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

  const selectWorkspace = (workspaceId: string) => {
    setActiveWorkspaceId(workspaceId)
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

  const deleteWorkspace = async (workspaceId: string) => {
    await coursesAPI.delete(Number(workspaceId))
    const remaining = workspaces.filter(
      (workspace) => workspace.id !== workspaceId,
    )
    setWorkspaces(remaining)

    if (activeWorkspaceId !== workspaceId) return
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
    (workspaceId: string, progress: number) => {
      setWorkspaces((current) => {
        const target = current.find((w) => w.id === workspaceId)
        if (!target || target.progress === progress) return current
        return current.map((w) =>
          w.id === workspaceId ? { ...w, progress } : w,
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
              isLoading={isLoadingWorkspaces}
              error={workspacesError}
              onRetry={() => fetchWorkspaces()}
              onCreate={createWorkspace}
              onSelect={selectWorkspace}
              onDelete={deleteWorkspace}
            />
          }
        />
        <Route
          path="/workspaces/:workspaceId"
          element={
            <WorkspaceRoute
              workspaces={workspaces}
              isLoading={isLoadingWorkspaces}
              onSelect={selectWorkspace}
              onUpdateProgress={updateWorkspaceProgress}
            />
          }
        />
        <Route
          path="/workspaces/:workspaceId/settings"
          element={
            <CourseSettingsRoute
              workspaces={workspaces}
              isLoading={isLoadingWorkspaces}
              onSelect={selectWorkspace}
              onSave={updateWorkspace}
              onDelete={deleteWorkspace}
            />
          }
        />
        <Route
          path="/workspaces/:workspaceId/progress"
          element={
            <ProgressRoute
              workspaces={workspaces}
              isLoading={isLoadingWorkspaces}
              onSelect={selectWorkspace}
            />
          }
        />
        <Route path="/workspaces/:workspaceId/edit" element={<LegacyEditRedirect />} />
        <Route path="/settings" element={<Navigate to="/dashboard" replace />} />
        <Route
          path="/profile"
          element={<AccountPage />}
        />
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
