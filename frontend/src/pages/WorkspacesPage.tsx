import { FormEvent, useEffect, useState } from 'react'
import {
  ArrowRight,
  BookOpen,
  CalendarDays,
  FileText,
  FolderOpen,
  Plus,
  Search,
  X,
} from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import WorkspaceNavigation from '../components/WorkspaceNavigation'
import type { Workspace, WorkspaceDraft } from '../data/workspaces'

type WorkspacesPageProps = {
  workspaces: Workspace[]
  activeWorkspaceId: string
  onCreate: (draft: WorkspaceDraft) => Promise<Workspace>
  onSelect: (workspaceId: string) => void
}

const emptyDraft: WorkspaceDraft = {
  name: '',
  semester: '',
  examDate: '',
  topics: '',
  syllabus: '',
}

function formatExamDate(date: string) {
  if (!date) return 'No exam date'

  return new Intl.DateTimeFormat('en', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(`${date}T00:00:00Z`))
}

function WorkspacesPage({
  workspaces,
  activeWorkspaceId,
  onCreate,
  onSelect,
}: WorkspacesPageProps) {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [isCreating, setIsCreating] = useState(false)
  const [draft, setDraft] = useState(emptyDraft)

  useEffect(() => {
    if (!isCreating) return

    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsCreating(false)
    }

    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [isCreating])

  const normalizedQuery = query.trim().toLowerCase()
  const filteredWorkspaces = workspaces.filter((workspace) => {
    const searchableText = [
      workspace.name,
      workspace.semester,
      ...workspace.topics,
    ]
      .join(' ')
      .toLowerCase()

    return searchableText.includes(normalizedQuery)
  })

  const updateDraft = (field: keyof WorkspaceDraft, value: string) => {
    setDraft((current) => ({ ...current, [field]: value }))
  }

  const createWorkspace = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    try {
      const workspace = await onCreate({ ...draft, name: draft.name.trim() })
      setDraft(emptyDraft)
      setIsCreating(false)
      navigate(`/workspaces/${workspace.id}`)
    } catch (error) {
      console.error("Error creating workspace", error)
    }
  }

  return (
    <main className="workspace-library-shell">
      <WorkspaceNavigation workspaceId={activeWorkspaceId} />

      <section className="workspace-library-content">
        <header className="workspace-library-header">
          <div>
            <p className="library-eyebrow">Lumina study spaces</p>
            <h1>Your Workspaces</h1>
            <p>
              Choose a course to continue studying or create a new workspace.
            </p>
          </div>

          <button
            className="create-workspace-button"
            type="button"
            onClick={() => setIsCreating(true)}
          >
            <Plus aria-hidden="true" />
            Create workspace
          </button>
        </header>

        <div className="workspace-toolbar">
          <label className="workspace-search">
            <Search aria-hidden="true" />
            <span className="visually-hidden">Search workspaces</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search by course, semester, or topic..."
            />
          </label>
          <p>
            {filteredWorkspaces.length}{' '}
            {filteredWorkspaces.length === 1 ? 'workspace' : 'workspaces'}
          </p>
        </div>

        {filteredWorkspaces.length > 0 ? (
          <div className="workspace-card-grid">
            {filteredWorkspaces.map((workspace) => (
              <Link
                className="workspace-card"
                to={`/workspaces/${workspace.id}`}
                onClick={() => onSelect(workspace.id)}
                key={workspace.id}
              >
                <article>
                  <header>
                    <span
                      className={`workspace-card-icon ${workspace.accent}`}
                      aria-hidden="true"
                    >
                      <BookOpen />
                    </span>
                    <span className="workspace-status">{workspace.status}</span>
                  </header>

                  <div className="workspace-card-title">
                    <p>{workspace.semester || 'Semester not specified'}</p>
                    <h2>{workspace.name}</h2>
                  </div>

                  <div className="workspace-card-meta">
                    <span>
                      <CalendarDays aria-hidden="true" />
                      {formatExamDate(workspace.examDate)}
                    </span>
                    <span>
                      <FileText aria-hidden="true" />
                      {workspace.sources.length}{' '}
                      {workspace.sources.length === 1 ? 'source' : 'sources'}
                    </span>
                  </div>

                  <div className="workspace-progress">
                    <span>
                      <strong>{workspace.progress}%</strong> course progress
                    </span>
                    <span className="progress-track" aria-hidden="true">
                      <span style={{ width: `${workspace.progress}%` }} />
                    </span>
                  </div>

                  <footer>
                    <span>{workspace.updatedAt}</span>
                    <strong>
                      Open workspace
                      <ArrowRight aria-hidden="true" />
                    </strong>
                  </footer>
                </article>
              </Link>
            ))}
          </div>
        ) : (
          <div className="workspace-empty-state">
            <FolderOpen aria-hidden="true" />
            <h2>No workspaces found</h2>
            <p>Try a different course name, semester, or topic.</p>
            <button type="button" onClick={() => setQuery('')}>
              Clear search
            </button>
          </div>
        )}
      </section>

      {isCreating && (
        <div className="workspace-modal-backdrop">
          <section
            className="workspace-modal panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="create-workspace-title"
          >
            <header>
              <div>
                <p className="library-eyebrow">New study space</p>
                <h2 id="create-workspace-title">Create Workspace</h2>
              </div>
              <button
                className="modal-close-button"
                type="button"
                aria-label="Close create workspace form"
                onClick={() => setIsCreating(false)}
              >
                <X aria-hidden="true" />
              </button>
            </header>

            <form onSubmit={createWorkspace}>
              <label className="form-field field-span-two">
                <span>
                  Course name <strong className="required-mark">Required</strong>
                </span>
                <input
                  autoFocus
                  value={draft.name}
                  onChange={(event) => updateDraft('name', event.target.value)}
                  placeholder="e.g. Introduction to Economics"
                  required
                  pattern=".*\S.*"
                  title="Course name cannot be empty"
                />
              </label>

              <label className="form-field">
                <span>
                  Semester <small>Optional</small>
                </span>
                <input
                  value={draft.semester}
                  onChange={(event) => updateDraft('semester', event.target.value)}
                  placeholder="e.g. Fall 2026"
                />
              </label>

              <label className="form-field">
                <span>
                  Exam date <small>Optional</small>
                </span>
                <input
                  type="date"
                  value={draft.examDate}
                  onChange={(event) => updateDraft('examDate', event.target.value)}
                />
              </label>

              <label className="form-field field-span-two">
                <span>
                  Topics <small>Optional</small>
                </span>
                <input
                  value={draft.topics}
                  onChange={(event) => updateDraft('topics', event.target.value)}
                  placeholder="Separate topics with commas"
                />
              </label>

              <label className="form-field field-span-two">
                <span>
                  Syllabus information <small>Optional</small>
                </span>
                <textarea
                  value={draft.syllabus}
                  onChange={(event) => updateDraft('syllabus', event.target.value)}
                  placeholder="Add a short course description or learning goals"
                />
              </label>

              <div className="workspace-modal-actions field-span-two">
                <button
                  className="secondary-button"
                  type="button"
                  onClick={() => setIsCreating(false)}
                >
                  Cancel
                </button>
                <button className="primary-button" type="submit">
                  Create workspace
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
    </main>
  )
}

export default WorkspacesPage
