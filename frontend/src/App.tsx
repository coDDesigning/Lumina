import { FormEvent, useEffect, useRef, useState, useCallback } from 'react'
import {
  CircleHelp,
  File,
  FilePlus2,
  Menu,
  Search,
} from 'lucide-react'
import { Navigate, Route, Routes, useParams } from 'react-router-dom'
import WorkspaceNavigation from './components/WorkspaceNavigation'
import type {
  Workspace,
  WorkspaceDraft,
  WorkspaceSource,
} from './data/workspaces'
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
import { Course } from './api/types'
import './App.css'
import './pages/pages.css'
import './pages/workspaces.css'

import { SummaryModal } from './components/study/SummaryModal'
import { QuizModal } from './components/study/QuizModal'
import { ProgressDashboard } from './components/study/ProgressDashboard'
import type { QuizResult } from './data/mockStudyData'

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
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('Exam')
  const [sources, setSources] = useState<WorkspaceSource[]>(workspace.sources)
  const [generatorPrompt, setGeneratorPrompt] = useState('')
  const [mainPrompt, setMainPrompt] = useState('')
  const [lastPrompt, setLastPrompt] = useState('')
  const [isSummaryModalOpen, setIsSummaryModalOpen] = useState(false)
  const [isQuizModalOpen, setIsQuizModalOpen] = useState(false)
  const [quizHistory, setQuizHistory] = useState<QuizResult[]>([])
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let isMounted = true;
    const loadDocuments = async () => {
      try {
        const docs = await coursesAPI.listDocuments(Number(workspace.id));
        if (isMounted) {
          setSources(docs.map(doc => ({
            id: doc.id,
            name: doc.original_file_name,
            description: `Status: ${doc.status}`
          })));
        }
      } catch (err) {
        console.error("Failed to load documents", err);
      }
    };
    loadDocuments();
    return () => { isMounted = false; };
  }, [workspace.id]);

  const addSources = async (files: FileList | null) => {
    if (!files?.length) return

    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      try {
        const response = await coursesAPI.uploadDocument(Number(workspace.id), file);
        const newSource: WorkspaceSource = {
          id: response.document.id,
          name: response.document.original_file_name,
          description: `Status: ${response.document.status}`,
        };
        setSources((current) => [...current, newSource]);
      } catch (err) {
        console.error("Failed to upload document", err);
      }
    }
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

  const handleQuizCompleted = (result: QuizResult) => {
    setQuizHistory(prev => [result, ...prev])
    if (onUpdateProgress) {
      const newProgress = Math.min(100, Math.max(workspace.progress, result.scorePercentage))
      onUpdateProgress(workspace.id, newProgress)
    }
  }

  const tabList: WorkspaceTab[] = ['Exam', 'Tutoring', 'Practice', 'Analytics']

  return (
    <main className="workspace-shell">
      <aside className="sidebar" aria-label="Study sources and prompt tools">
        <section className="panel sources-panel">
          <header className="panel-header">
            <h1>Sources</h1>
          </header>

          <div className="source-list" aria-live="polite">
            {sources.map((source) => (
              <article className="source-item" key={source.id}>
                <File aria-hidden="true" strokeWidth={2.1} />
                <div>
                  <h2>{source.name}</h2>
                  <p>{source.description}</p>
                </div>
              </article>
            ))}
          </div>

          <div className="add-source-row">
            <input
              ref={fileInputRef}
              className="visually-hidden"
              type="file"
              multiple
              accept=".pdf,.txt,.md"
              onChange={(event) => {
                addSources(event.target.files)
                event.target.value = ''
              }}
            />
            <button
              className="text-action"
              type="button"
              onClick={() => fileInputRef.current?.click()}
            >
              <FilePlus2 aria-hidden="true" strokeWidth={2.1} />
              Add Sources
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
              topics={workspace.topics}
              documentCount={sources.length}
              quizHistory={quizHistory}
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

                {lastPrompt && (
                  <p className="local-status" role="status">
                    Prompt saved locally: "{lastPrompt}"
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

      <SummaryModal
        isOpen={isSummaryModalOpen}
        onClose={() => setIsSummaryModalOpen(false)}
        courseName={workspace.name}
        topics={workspace.topics}
      />

      <QuizModal
        isOpen={isQuizModalOpen}
        onClose={() => setIsQuizModalOpen(false)}
        courseName={workspace.name}
        topics={workspace.topics}
        onQuizCompleted={handleQuizCompleted}
      />
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
    syllabus: course.description || '',
    progress: 0,
    status: 'In progress',
    updatedAt: new Date(course.created_at).toLocaleDateString(),
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
        description: draft.syllabus.trim(),
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
        description: updatedWorkspace.syllabus.trim(),
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

  const updateWorkspaceProgress = (workspaceId: string, progress: number) => {
    setWorkspaces((current) =>
      current.map((w) => (w.id === workspaceId ? { ...w, progress } : w))
    );
  };

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
