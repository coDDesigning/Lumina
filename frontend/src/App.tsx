import { FormEvent, useEffect, useRef, useState } from 'react'
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
import { initialWorkspaces } from './data/workspaces'
import EditPage from './pages/EditPage'
import ProfilePage from './pages/ProfilePage'
import SettingsPage from './pages/SettingsPage'
import WorkspacesPage from './pages/WorkspacesPage'
import './App.css'
import './pages/pages.css'
import './pages/workspaces.css'

type WorkspaceTab = 'Exam' | 'Tutoring' | 'Practice'

const tabContent: Record<
  WorkspaceTab,
  { body: string; suggestions: string[] }
> = {
  Exam: {
    body: `Individuals, when faced with dire situations, often possess the tendency to seek comfort in any form that is accessible to them, even resorting to mental fabrication at times to conjure up the very comfort they had initially sought. This innate pursuit of comfort can manifest as self manipulation, as individuals try to alter their perception of the current conditions in their favor, creating a more optimal situation. This is accomplished by originating a mental barrier in between the cause of an individual's discomfort and the individual themselves with the aim of limiting further exposure and thus, further discomfort. This method is often utilized when individuals alter their perception to avoid the emotional toll that taking proper accountability entails. By reshaping their perception, individuals aim to avoid this emotional toll of undertaking liability, actively favoring comfort over truth. Instead of facing the ethical consequences of their actions, individuals generally opt for the easier route, where they delicately reconstruct their perception of their current situation to minimize their culpability in both their own and everyone's perspective. For instance, people who actively partake in such activities, might be inclined to blame external influences, rather than undertaking necessary liability. An individual who acts negligent towards a responsibility of theirs to such a degree that they pass a certain point, where nothing of significance can be done about aforementioned responsibility, might find placing the blame onto outside circumstances more palatable and comforting. This inclination to shift the blame is fueled by individuals' escapist tendencies which aspire to alleviate the concomitant discomfort that accompanies the process of taking accountability. As a result, self-manipulation expectedly becomes an effective vessel utilized for escapism, as it helps individuals form metaphorical barriers in between themselves and the moral implications of their actions, albeit not offering a remedy of any sorts for the affected party.`,
    suggestions: [
      'Generate multi-choice problems',
      'Generate open ended problems',
      'Generate summary',
      'Make comparisons with specific sources',
    ],
  },
  Tutoring: {
    body: `Turn your uploaded material into a focused tutoring session. Lumina can explain difficult ideas one step at a time, connect related concepts, and adapt each explanation to the questions you ask. Choose a suggestion below or enter a topic you would like to understand better.`,
    suggestions: [
      'Explain the central argument',
      'Teach this topic step by step',
      'Give me an everyday example',
      'Ask me a guiding question',
    ],
  },
  Practice: {
    body: `Build a practice session from the sources in this workspace. You can review key concepts, answer questions at your own pace, and identify topics that need more attention before the exam.`,
    suggestions: [
      'Start a quick practice set',
      'Create true or false questions',
      'Practice my weakest topic',
      'Review my answers',
    ],
  },
}

type WorkspacePageProps = {
  workspace: Workspace
}

function WorkspacePage({ workspace }: WorkspacePageProps) {
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('Exam')
  const [sources, setSources] = useState<WorkspaceSource[]>(workspace.sources)
  const [generatorPrompt, setGeneratorPrompt] = useState('')
  const [mainPrompt, setMainPrompt] = useState('')
  const [lastPrompt, setLastPrompt] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  const addSources = (files: FileList | null) => {
    if (!files?.length) return

    const nextId = Math.max(0, ...sources.map(({ id }) => id)) + 1
    const addedSources = Array.from(files).map((file, index) => ({
      id: nextId + index,
      name: file.name,
      description: 'Ready for local preview.',
    }))

    setSources((current) => [...current, ...addedSources])
  }

  const generatePrompt = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const request = generatorPrompt.trim()
    if (!request) return

    setMainPrompt(`Create a clear study activity about: ${request}`)
    setGeneratorPrompt('')
  }

  const submitPrompt = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const prompt = mainPrompt.trim()
    if (!prompt) return

    setLastPrompt(prompt)
    setMainPrompt('')
  }

  const chooseSuggestion = (suggestion: string) => {
    setMainPrompt(suggestion)
  }

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
              Enter a description of your desired prompt to generate a prompt
              best suited for the AI to maximize efficiency.
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
              placeholder="Enter prompt description."
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
            {(Object.keys(tabContent) as WorkspaceTab[]).map((tab) => (
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

          <section className="panel chat-panel" role="tabpanel">
            <header className="panel-header chat-header">
              <h2>Chat</h2>
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
                placeholder="Enter prompt."
              />
              <button type="submit" aria-label="Submit prompt">
                <Search aria-hidden="true" />
              </button>
            </form>
          </section>
        </div>
      </section>
    </main>
  )
}

const WORKSPACES_STORAGE_KEY = 'lumina.workspaces'
const ACTIVE_WORKSPACE_STORAGE_KEY = 'lumina.activeWorkspaceId'
const workspaceAccents: Workspace['accent'][] = [
  'blue',
  'violet',
  'rose',
  'amber',
]

function loadWorkspaces() {
  const storedWorkspaces = localStorage.getItem(WORKSPACES_STORAGE_KEY)
  if (!storedWorkspaces) return initialWorkspaces

  try {
    const parsedWorkspaces = JSON.parse(storedWorkspaces) as Workspace[]
    return Array.isArray(parsedWorkspaces) && parsedWorkspaces.length > 0
      ? parsedWorkspaces
      : initialWorkspaces
  } catch {
    return initialWorkspaces
  }
}

type WorkspaceRouteProps = {
  workspaces: Workspace[]
  onSelect: (workspaceId: string) => void
}

function WorkspaceRoute({ workspaces, onSelect }: WorkspaceRouteProps) {
  const { workspaceId } = useParams()
  const workspace = workspaces.find(({ id }) => id === workspaceId)

  useEffect(() => {
    if (workspace) onSelect(workspace.id)
  }, [onSelect, workspace])

  if (!workspace) return <Navigate to="/" replace />
  return <WorkspacePage key={workspace.id} workspace={workspace} />
}

type EditWorkspaceRouteProps = WorkspaceRouteProps & {
  onSave: (workspace: Workspace) => void
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

function App() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>(loadWorkspaces)
  const [activeWorkspaceId, setActiveWorkspaceId] = useState(
    () =>
      localStorage.getItem(ACTIVE_WORKSPACE_STORAGE_KEY) ??
      initialWorkspaces[0].id,
  )

  useEffect(() => {
    localStorage.setItem(WORKSPACES_STORAGE_KEY, JSON.stringify(workspaces))
  }, [workspaces])

  useEffect(() => {
    localStorage.setItem(ACTIVE_WORKSPACE_STORAGE_KEY, activeWorkspaceId)
  }, [activeWorkspaceId])

  const selectWorkspace = (workspaceId: string) => {
    setActiveWorkspaceId(workspaceId)
  }

  const createWorkspace = (draft: WorkspaceDraft) => {
    const slugBase =
      draft.name
        .toLowerCase()
        .trim()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-|-$/g, '') || 'workspace'
    let id = slugBase
    let suffix = 2

    while (workspaces.some((workspace) => workspace.id === id)) {
      id = `${slugBase}-${suffix}`
      suffix += 1
    }

    const workspace: Workspace = {
      id,
      name: draft.name.trim(),
      semester: draft.semester.trim(),
      examDate: draft.examDate,
      topics: draft.topics
        .split(',')
        .map((topic) => topic.trim())
        .filter(Boolean),
      syllabus: draft.syllabus.trim(),
      progress: 0,
      status: 'New',
      updatedAt: 'Created just now',
      accent: workspaceAccents[workspaces.length % workspaceAccents.length],
      sources: [],
    }

    setWorkspaces((current) => [workspace, ...current])
    setActiveWorkspaceId(workspace.id)
    return workspace
  }

  const updateWorkspace = (updatedWorkspace: Workspace) => {
    setWorkspaces((current) =>
      current.map((workspace) =>
        workspace.id === updatedWorkspace.id ? updatedWorkspace : workspace,
      ),
    )
  }

  return (
    <Routes>
      <Route
        path="/"
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
          <WorkspaceRoute workspaces={workspaces} onSelect={selectWorkspace} />
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
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
