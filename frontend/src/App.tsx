import { FormEvent, useEffect, useRef, useState, useCallback } from 'react'
import {
  CircleHelp,
  FilePlus2,
  Menu,
  Search,
} from 'lucide-react'
import { Navigate, Route, Routes, useParams } from 'react-router-dom'
import WorkspaceNavigation from './components/WorkspaceNavigation'
import type { Workspace, WorkspaceDraft } from './data/workspaces'
import EditPage from './pages/EditPage'
import ProfilePage from './pages/ProfilePage'
import SettingsPage from './pages/SettingsPage'
import WorkspacesPage from './pages/WorkspacesPage'
import LandingPage from './pages/LandingPage'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'
import { ProtectedRoute } from './components/ProtectedRoute'
import { useAuth } from './context/AuthContext'
import { coursesAPI } from './api/courses'
import { progressAPI } from './api/progress'
import { courseQaAPI } from './api/courseQa'
import { describeError, describeUploadError, isAbortError } from './api/errors'
import type { Course, CourseProgressResponse } from './api/types'
import { useCourseDocuments } from './hooks/useCourseDocuments'
import { DocumentRow } from './components/documents/DocumentRow'
import './App.css'
import './pages/pages.css'
import './pages/workspaces.css'

import { SummaryModal } from './components/study/SummaryModal'
import { QuizModal } from './components/study/QuizModal'
import { ProgressDashboard } from './components/study/ProgressDashboard'

type WorkspaceTab = 'Exam' | 'Tutoring' | 'Practice' | 'Analytics'

const tabContent: Record<
  Exclude<WorkspaceTab, 'Analytics'>,
  { body: string; suggestions: string[] }
> = {
  Exam: {
    body: `Individuals, when faced with dire situations, often possess the tendency to seek comfort in any form that is accessible to them, even resorting to mental fabrication at times to conjure up the very comfort they had initially sought. This innate pursuit of comfort can manifest as self manipulation, as individuals try to alter their perception of the current conditions in their favor, creating a more optimal situation. This is accomplished by originating a mental barrier in between the cause of an individual's discomfort and the individual themselves with the aim of limiting further exposure and thus, further discomfort. This method is often utilized when individuals alter their perception to avoid the emotional toll that taking proper accountability entails. By reshaping their perception, individuals aim to avoid this emotional toll of undertaking liability, actively favoring comfort over truth. Instead of facing the ethical consequences of their actions, individuals generally opt for the easier route, where they delicately reconstruct their perception of their current situation to minimize their culpability in both their own and everyone's perspective. For instance, people who actively partake in such activities, might be inclined to blame external influences, rather than undertaking necessary liability. An individual who acts negligent towards a responsibility of theirs to such a degree that they pass a certain point, where nothing of significance can be done about aforementioned responsibility, might find placing the blame onto outside circumstances more palatable and comforting. This inclination to shift the blame is fueled by individuals' escapist tendencies which aspire to alleviate the concomitant discomfort that accompanies the process of taking accountability. As a result, self-manipulation expectedly becomes an effective vessel utilized for escapism, as it helps individuals form metaphorical barriers in between themselves and the moral implications of their actions, albeit not offering a remedy of any sorts for the affected party.`,
    suggestions: [
      'Generate summary',
      'Start a quick practice set',
      'Generate multi-choice problems',
      'Make comparisons with specific sources',
    ],
  },
  Tutoring: {
    body: `Turn your uploaded material into a focused tutoring session. Lumina can explain difficult ideas one step at a time, connect related concepts, and adapt each explanation to the questions you ask. Choose a suggestion below or enter a topic you would like to understand better.`,
    suggestions: [
      'Explain the central argument',
      'Teach this topic step by step',
      'Generate summary',
      'Ask me a guiding question',
    ],
  },
  Practice: {
    body: `Build a practice session from the sources in this workspace. You can review key concepts, answer questions at your own pace, and identify topics that need more attention before the exam.`,
    suggestions: [
      'Start a quick practice set',
      'Create true or false questions',
      'Practice my weakest topic',
      'Generate summary',
    ],
  },
}

type WorkspacePageProps = {
  workspace: Workspace
  onUpdateProgress?: (workspaceId: string, progress: number) => void
}

function WorkspacePage({ workspace, onUpdateProgress }: WorkspacePageProps) {
  const courseId = Number(workspace.id)
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('Exam')
  const [generatorPrompt, setGeneratorPrompt] = useState('')
  const [mainPrompt, setMainPrompt] = useState('')
  const [lastPrompt, setLastPrompt] = useState('')
  const [isSummaryModalOpen, setIsSummaryModalOpen] = useState(false)
  const [isQuizModalOpen, setIsQuizModalOpen] = useState(false)
  const [uploadErrors, setUploadErrors] = useState<{ fileName: string; message: string }[]>([])
  const [uploadNotices, setUploadNotices] = useState<string[]>([])
  const [uploadProgress, setUploadProgress] = useState<{ done: number; total: number } | null>(null)
  const [progress, setProgress] = useState<CourseProgressResponse | null>(null)
  const [isProgressLoading, setIsProgressLoading] = useState(false)
  const [progressError, setProgressError] = useState<string | null>(null)
  const [progressToken, setProgressToken] = useState(0)
  const [qaResult, setQaResult] = useState<{ question: string; answer: string; truncated?: boolean } | null>(null)
  const [isQaLoading, setIsQaLoading] = useState(false)
  const [qaError, setQaError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const {
    entries,
    isLoading: areDocumentsLoading,
    listError,
    readyCount,
    reload,
    addUploaded,
    retryDocument,
    deleteDocument,
  } = useCourseDocuments(courseId)

  useEffect(() => {
    if (!Number.isInteger(courseId) || courseId <= 0) return

    const controller = new AbortController()
    let cancelled = false

    setIsProgressLoading(true)
    setProgressError(null)

    progressAPI
      .get(courseId, { signal: controller.signal })
      .then((result) => {
        if (cancelled) return
        setProgress(result)
        setIsProgressLoading(false)
      })
      .catch((error: unknown) => {
        if (cancelled || isAbortError(error)) return
        setIsProgressLoading(false)
        setProgressError(describeError(error, 'Progress could not be loaded.').message)
      })

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [courseId, progressToken])

  useEffect(() => {
    if (!onUpdateProgress || progress?.average_score == null) return
    onUpdateProgress(workspace.id, Math.round(progress.average_score * 100))
  }, [onUpdateProgress, progress, workspace.id])

  const addSources = async (fileList: FileList | null) => {
    const files = Array.from(fileList ?? [])
    if (files.length === 0) return

    setUploadErrors([])
    setUploadNotices([])
    setUploadProgress({ done: 0, total: files.length })

    const errors: { fileName: string; message: string }[] = []
    const notices: string[] = []

    for (const file of files) {
      try {
        const response = await coursesAPI.uploadDocument(courseId, file)
        addUploaded(response.document)
        if (response.duplicate) {
          notices.push(`${file.name} is already in this course.`)
        }
      } catch (error) {
        errors.push({ fileName: file.name, message: describeUploadError(error).message })
      } finally {
        setUploadProgress((current) =>
          current ? { ...current, done: current.done + 1 } : current
        )
      }
    }

    setUploadErrors(errors)
    setUploadNotices(notices)
    setUploadProgress(null)
  }

  const generatePrompt = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const request = generatorPrompt.trim()
    if (!request) return

    if (request.toLowerCase().includes('summary') || request.toLowerCase().includes('özet')) {
      setIsSummaryModalOpen(true)
      setGeneratorPrompt('')
      return
    }

    if (request.toLowerCase().includes('quiz') || request.toLowerCase().includes('soru') || request.toLowerCase().includes('test')) {
      setIsQuizModalOpen(true)
      setGeneratorPrompt('')
      return
    }

    setMainPrompt(`Create a clear study activity about: ${request}`)
    setGeneratorPrompt('')
  }

  const submitPrompt = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const prompt = mainPrompt.trim()
    if (!prompt) return

    if (prompt.toLowerCase().includes('summary') || prompt.toLowerCase().includes('özet')) {
      setIsSummaryModalOpen(true)
      setMainPrompt('')
      return
    }

    if (prompt.toLowerCase().includes('quiz') || prompt.toLowerCase().includes('test') || prompt.toLowerCase().includes('practice')) {
      setIsQuizModalOpen(true)
      setMainPrompt('')
      return
    }

    setLastPrompt(prompt)
    setMainPrompt('')
    setIsQaLoading(true)
    setQaError(null)

    courseQaAPI
      .ask(courseId, { question: prompt })
      .then((res) => {
        setQaResult({
          question: prompt,
          answer: res.answer,
          truncated: res.context_truncated,
        })
      })
      .catch((err: unknown) => {
        setQaError(describeError(err, 'Failed to generate answer from course materials.').message)
      })
      .finally(() => {
        setIsQaLoading(false)
      })
  }

  const chooseSuggestion = (suggestion: string) => {
    if (suggestion === 'Generate summary') {
      setIsSummaryModalOpen(true)
      return
    }
    if (suggestion === 'Start a quick practice set' || suggestion === 'Create true or false questions' || suggestion === 'Generate multi-choice problems') {
      setIsQuizModalOpen(true)
      return
    }
    setMainPrompt(suggestion)
  }

  const tabList: WorkspaceTab[] = ['Exam', 'Tutoring', 'Practice', 'Analytics']
  const processingCount = entries.filter(
    (entry) => entry.document.status === 'uploaded' || entry.document.status === 'processing'
  ).length

  return (
    <main className="workspace-shell">
      <aside className="sidebar" aria-label="Study sources and prompt tools">
        <section className="panel sources-panel">
          <header className="panel-header">
            <h1>Sources</h1>
          </header>

          <p className="visually-hidden" role="status">
            {processingCount > 0
              ? `${processingCount} source${processingCount === 1 ? '' : 's'} still processing`
              : 'All sources are up to date'}
          </p>

          {listError ? (
            <p className="source-list-message" role="alert">
              {listError}{' '}
              <button className="text-action" type="button" onClick={reload}>
                Reload
              </button>
            </p>
          ) : null}

          {uploadErrors.length > 0 ? (
            <ul className="source-upload-errors" role="alert">
              {uploadErrors.map((failure) => (
                <li key={failure.fileName}>
                  {failure.fileName}: {failure.message}
                </li>
              ))}
            </ul>
          ) : null}

          {uploadNotices.length > 0 ? (
            <ul className="source-upload-errors source-upload-notices">
              {uploadNotices.map((notice) => (
                <li key={notice}>{notice}</li>
              ))}
            </ul>
          ) : null}

          <div className="source-list">
            {areDocumentsLoading && entries.length === 0 ? (
              <p className="source-list-message">Loading sources…</p>
            ) : null}

            {!areDocumentsLoading && entries.length === 0 && !listError ? (
              <p className="source-list-message">
                No sources yet. Add a PDF, TXT, or Markdown file to get started.
              </p>
            ) : null}

            {entries.map((entry) => (
              <DocumentRow
                key={entry.document.id}
                entry={entry}
                onRetry={retryDocument}
                onDelete={deleteDocument}
              />
            ))}
          </div>

          <div className="add-source-row">
            <input
              ref={fileInputRef}
              className="visually-hidden"
              type="file"
              multiple
              accept=".pdf,.txt,.md,.markdown"
              onChange={(event) => {
                addSources(event.target.files)
                event.target.value = ''
              }}
            />
            <button
              className="text-action"
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploadProgress !== null}
            >
              <FilePlus2 aria-hidden="true" strokeWidth={2.1} />
              {uploadProgress
                ? `Uploading ${uploadProgress.done} of ${uploadProgress.total}…`
                : 'Add Sources'}
            </button>
          </div>
        </section>

        <section className="panel generator-panel">
          <header className="panel-header">
            <h2>Prompt Generator</h2>
          </header>

          <div className="generator-description">
            <CircleHelp aria-hidden="true" strokeWidth={2.2} />
            <p>
              Enter a description of your desired prompt to generate study activities, summaries, or quizzes.
            </p>
          </div>

          <form className="prompt-field" onSubmit={generatePrompt}>
            <Menu aria-hidden="true" />
            <label className="visually-hidden" htmlFor="generator-prompt">
              Prompt description
            </label>
            <input
              id="generator-prompt"
              value={generatorPrompt}
              onChange={(event) => setGeneratorPrompt(event.target.value)}
              placeholder="e.g. Generate summary or Quiz"
            />
            <button type="submit" aria-label="Generate prompt">
              <Search aria-hidden="true" />
            </button>
          </form>
        </section>
      </aside>

      <section className="main-workspace">
        <WorkspaceNavigation workspaceId={workspace.id} />

        <div className="workspace-stage">
          <div className="workspace-tabs" role="tablist" aria-label="Study mode">
            {tabList.map((tab) => (
              <button
                className={activeTab === tab ? 'active' : ''}
                type="button"
                role="tab"
                aria-selected={activeTab === tab}
                key={tab}
                onClick={() => setActiveTab(tab)}
              >
                {tab}
              </button>
            ))}
          </div>

          {activeTab === 'Analytics' ? (
            <ProgressDashboard
              courseName={workspace.name}
              documentCount={entries.length}
              readyDocumentCount={readyCount}
              progress={progress}
              isLoading={isProgressLoading}
              error={progressError}
              onOpenQuizModal={() => setIsQuizModalOpen(true)}
              onOpenSummaryModal={() => setIsSummaryModalOpen(true)}
            />
          ) : (
            <section className="panel chat-panel" role="tabpanel">
              <header className="panel-header chat-header" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <h2>Chat & Study Tools</h2>
                <div style={{ display: 'flex', gap: '8px' }}>
                  <button
                    type="button"
                    className="secondary-button"
                    style={{ padding: '6px 12px', fontSize: '13px' }}
                    onClick={() => setIsSummaryModalOpen(true)}
                  >
                    ✨ Summary
                  </button>
                  <button
                    type="button"
                    className="primary-button"
                    style={{ padding: '6px 12px', fontSize: '13px' }}
                    onClick={() => setIsQuizModalOpen(true)}
                  >
                    🎯 Practice Quiz
                  </button>
                </div>
              </header>

              <div className="chat-scroll">
                <p className="response-copy">{tabContent[activeTab].body}</p>

                <div className="suggestions" aria-label="Suggested prompts">
                  {tabContent[activeTab].suggestions.map((suggestion) => (
                    <button
                      type="button"
                      key={suggestion}
                      onClick={() => chooseSuggestion(suggestion)}
                    >
                      <CircleHelp aria-hidden="true" strokeWidth={2.2} />
                      <span>{suggestion}</span>
                    </button>
                  ))}
                </div>

                {isQaLoading && (
                  <div className="qa-loading-indicator" role="status" style={{ padding: '12px', background: 'var(--color-surface, #f5f5f5)', borderRadius: '8px', margin: '12px 0' }}>
                    <p style={{ margin: 0, fontStyle: 'italic' }}>🔍 Searching course materials and generating answer…</p>
                  </div>
                )}

                {qaError && (
                  <div className="qa-error-alert" role="alert" style={{ padding: '12px', background: 'rgba(239, 68, 68, 0.1)', color: '#dc2626', borderRadius: '8px', margin: '12px 0' }}>
                    <p style={{ margin: 0 }}>{qaError}</p>
                  </div>
                )}

                {qaResult && (
                  <div className="qa-result-card" style={{ padding: '16px', background: 'var(--color-surface, #f8fafc)', border: '1px solid var(--color-border, #e2e8f0)', borderRadius: '8px', margin: '16px 0' }}>
                    <p style={{ fontWeight: 600, margin: '0 0 8px 0', color: 'var(--color-text-primary, #0f172a)' }}>Q: {qaResult.question}</p>
                    <p style={{ whiteSpace: 'pre-wrap', margin: '0 0 8px 0', lineHeight: 1.6 }}>{qaResult.answer}</p>
                    {qaResult.truncated && (
                      <small style={{ color: '#d97706', display: 'block' }}>⚠️ Note: Only a portion of course materials was used due to length.</small>
                    )}
                  </div>
                )}

                {lastPrompt && !qaResult && !isQaLoading && !qaError && (
                  <p className="local-status" role="status">
                    Prompt sent: "{lastPrompt}"
                  </p>
                )}
              </div>

              <form className="prompt-field main-prompt" onSubmit={submitPrompt}>
                <Menu aria-hidden="true" />
                <label className="visually-hidden" htmlFor="main-prompt">
                  Enter prompt
                </label>
                <input
                  id="main-prompt"
                  value={mainPrompt}
                  onChange={(event) => setMainPrompt(event.target.value)}
                  placeholder="Enter prompt (type 'summary' or 'quiz' for quick tools)..."
                />
                <button type="submit" aria-label="Submit prompt">
                  <Search aria-hidden="true" />
                </button>
              </form>
            </section>
          )}
        </div>
      </section>

      {isSummaryModalOpen ? (
        <SummaryModal
          courseId={courseId}
          courseName={workspace.name}
          topics={workspace.topics}
          readyDocumentCount={readyCount}
          onClose={() => setIsSummaryModalOpen(false)}
        />
      ) : null}

      {isQuizModalOpen ? (
        <QuizModal
          courseId={courseId}
          topics={workspace.topics}
          readyDocumentCount={readyCount}
          onClose={() => setIsQuizModalOpen(false)}
          onAttemptRecorded={() => setProgressToken((token) => token + 1)}
        />
      ) : null}
    </main>
  )
}

const ACTIVE_WORKSPACE_STORAGE_KEY = 'lumina.activeWorkspaceId'
const workspaceAccents: Workspace['accent'][] = [
  'blue',
  'violet',
  'rose',
  'amber',
]

type WorkspaceRouteProps = {
  workspaces: Workspace[]
  onSelect: (workspaceId: string) => void
  onUpdateProgress?: (workspaceId: string, progress: number) => void
}

function WorkspaceRoute({ workspaces, onSelect, onUpdateProgress }: WorkspaceRouteProps) {
  const { workspaceId } = useParams()
  const workspace = workspaces.find(({ id }) => id === workspaceId)

  useEffect(() => {
    if (workspace) onSelect(workspace.id)
  }, [onSelect, workspace])

  if (!workspace) return <Navigate to="/" replace />
  return (
    <WorkspacePage
      key={workspace.id}
      workspace={workspace}
      onUpdateProgress={onUpdateProgress}
    />
  )
}

type EditWorkspaceRouteProps = WorkspaceRouteProps & {
  onSave: (workspace: Workspace) => Promise<void> | void
}

function EditWorkspaceRoute({
  workspaces,
  onSelect,
  onSave,
}: EditWorkspaceRouteProps) {
  const { workspaceId } = useParams()
  const workspace = workspaces.find(({ id }) => id === workspaceId)

  useEffect(() => {
    if (workspace) onSelect(workspace.id)
  }, [onSelect, workspace])

  if (!workspace) return <Navigate to="/" replace />
  return <EditPage key={workspace.id} workspace={workspace} onSave={onSave} />
}

function mapCourseToWorkspace(course: Course, index: number): Workspace {
  return {
    id: course.id.toString(),
    name: course.title,
    semester: course.semester || '',
    examDate: course.exam_date || '',
    topics: course.topics ? course.topics.split(',').map(t => t.trim()) : [],
    syllabus: course.syllabus || '',
    progress: 0,
    status: 'In progress',
    updatedAt: new Date(course.updated_at).toLocaleDateString(),
    accent: workspaceAccents[index % workspaceAccents.length],
    sources: [],
  };
}

function App() {
  const { isAuthenticated } = useAuth()
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  
  const [activeWorkspaceId, setActiveWorkspaceId] = useState(
    () => localStorage.getItem(ACTIVE_WORKSPACE_STORAGE_KEY) ?? ''
  )

  const fetchWorkspaces = useCallback(async () => {
    if (!isAuthenticated) return;
    try {
      const courses = await coursesAPI.list();
      const mappedWorkspaces = courses.map((course, index) => mapCourseToWorkspace(course, index));
      setWorkspaces(mappedWorkspaces);
      
      setActiveWorkspaceId((current) => {
        if (mappedWorkspaces.length > 0 && !current) {
          return mappedWorkspaces[0].id;
        }
        return current;
      });
    } catch (error) {
      console.error("Failed to load workspaces", error);
    }
  }, [isAuthenticated]);

  useEffect(() => {
    fetchWorkspaces();
  }, [fetchWorkspaces]);

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
        syllabus: draft.syllabus.trim(),
        semester: draft.semester.trim(),
        exam_date: draft.examDate,
        topics: draft.topics,
      });
      
      const newWorkspace = mapCourseToWorkspace(newCourse, workspaces.length);
      setWorkspaces(current => [newWorkspace, ...current]);
      setActiveWorkspaceId(newWorkspace.id);
      return newWorkspace;
    } catch (error) {
      console.error("Failed to create workspace", error);
      throw error;
    }
  }

  const updateWorkspace = async (updatedWorkspace: Workspace) => {
    try {
      const updatedCourse = await coursesAPI.update(Number(updatedWorkspace.id), {
        title: updatedWorkspace.name.trim(),
        syllabus: updatedWorkspace.syllabus.trim(),
        semester: updatedWorkspace.semester.trim(),
        exam_date: updatedWorkspace.examDate,
        topics: updatedWorkspace.topics.join(', '),
      });
      
      const updatedMappedWorkspace = mapCourseToWorkspace(updatedCourse, workspaces.findIndex(w => w.id === updatedWorkspace.id));
      setWorkspaces(current =>
        current.map(workspace =>
          workspace.id === updatedWorkspace.id ? updatedMappedWorkspace : workspace
        )
      );
    } catch (error) {
      console.error("Failed to update workspace", error);
    }
  }

  const updateWorkspaceProgress = useCallback((workspaceId: string, progress: number) => {
    setWorkspaces((current) => {
      const target = current.find((w) => w.id === workspaceId);
      if (!target || target.progress === progress) return current;
      return current.map((w) => (w.id === workspaceId ? { ...w, progress } : w));
    });
  }, []);

  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      
      <Route element={<ProtectedRoute />}>
        <Route
          path="/dashboard"
          element={
            <WorkspacesPage
              workspaces={workspaces}
              activeWorkspaceId={activeWorkspaceId}
              onCreate={createWorkspace}
              onSelect={selectWorkspace}
            />
          }
        />
        <Route
          path="/workspaces/:workspaceId"
          element={
            <WorkspaceRoute
              workspaces={workspaces}
              onSelect={selectWorkspace}
              onUpdateProgress={updateWorkspaceProgress}
            />
          }
        />
        <Route
          path="/workspaces/:workspaceId/edit"
          element={
            <EditWorkspaceRoute
              workspaces={workspaces}
              onSelect={selectWorkspace}
              onSave={updateWorkspace}
            />
          }
        />
        <Route
          path="/settings"
          element={<SettingsPage workspaceId={activeWorkspaceId} />}
        />
        <Route
          path="/profile"
          element={<ProfilePage workspaceId={activeWorkspaceId} />}
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
