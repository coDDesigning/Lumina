import { FormEvent, useEffect, useState } from 'react'
import {
  AlertCircle,
  ArrowRight,
  BookOpen,
  CalendarDays,
  FileText,
  FolderOpen,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  X,
} from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { describeError } from '../api/errors'
import type { Workspace, WorkspaceDraft } from '../data/workspaces'
import {
  EDUCATION_LEVEL_LABELS,
  type EducationLevel,
} from '../api/types'

type WorkspacesPageProps = {
  workspaces: Workspace[]
  isLoading?: boolean
  error?: string | null
  onRetry?: () => void
  onCreate: (draft: WorkspaceDraft) => Promise<Workspace>
  onSelect: (workspaceId: string) => void
  onDelete: (workspaceId: string) => Promise<void>
}

const deleteWarning =
  'This permanently erases the course, its uploaded files, and everything generated from them. It cannot be undone.'

const emptyDraft: WorkspaceDraft = {
  name: '',
  subjectArea: '',
  educationLevel: 'unspecified',
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
  isLoading = false,
  error = null,
  onRetry,
  onCreate,
  onSelect,
  onDelete,
}: WorkspacesPageProps) {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [isCreating, setIsCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [draft, setDraft] = useState(emptyDraft)
  const [confirmingId, setConfirmingId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<{
    id: string
    message: string
  } | null>(null)

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

  const deleteWorkspace = async (workspaceId: string) => {
    setConfirmingId(null)
    setDeleteError(null)
    setDeletingId(workspaceId)
    try {
      await onDelete(workspaceId)
    } catch (err) {
      const described = describeError(
        err,
        'The course could not be deleted. Try again.'
      )
      setDeleteError({ id: workspaceId, message: described.message })
    } finally {
      setDeletingId(null)
    }
  }

  const handleOpenCreate = () => {
    setCreateError(null)
    setIsCreating(true)
  }

  const createWorkspace = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setCreateError(null)
    try {
      const workspace = await onCreate({ ...draft, name: draft.name.trim() })
      setDraft(emptyDraft)
      setIsCreating(false)
      navigate(`/workspaces/${workspace.id}`)
    } catch (err) {
      const described = describeError(
        err,
        'Failed to create workspace. Please try again.'
      )
      setCreateError(described.message)
    }
  }

  return (
    <main className="workspace-library-shell">
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
            onClick={handleOpenCreate}
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

        {error ? (
          <div
            className="workspace-error-state"
            role="alert"
            style={{
              textAlign: 'center',
              padding: '48px 16px',
              background: '#fef2f2',
              border: '1px solid #fecaca',
              borderRadius: '12px',
              margin: '24px 0',
            }}
          >
            <AlertCircle
              aria-hidden="true"
              style={{
                width: '36px',
                height: '36px',
                color: '#ef4444',
                margin: '0 auto 12px',
              }}
            />
            <h2 style={{ fontSize: '18px', color: '#991b1b', margin: '0 0 8px' }}>
              Failed to load workspaces
            </h2>
            <p style={{ color: '#7f1d1d', margin: '0 0 16px', fontSize: '14px' }}>
              {error}
            </p>
            {onRetry && (
              <button
                type="button"
                className="secondary-button"
                onClick={onRetry}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '6px',
                  margin: '0 auto',
                }}
              >
                <RefreshCw style={{ width: '14px', height: '14px' }} />
                Retry
              </button>
            )}
          </div>
        ) : isLoading ? (
          <div
            className="workspace-loading-state"
            style={{ textAlign: 'center', padding: '48px 16px', color: '#64748b' }}
          >
            <p>Loading workspaces...</p>
          </div>
        ) : filteredWorkspaces.length > 0 ? (
          <div className="workspace-card-grid">
            {filteredWorkspaces.map((workspace) => (
              <div className="workspace-card-shell" key={workspace.id}>
                <Link
                  className="workspace-card"
                  to={`/workspaces/${workspace.id}`}
                  onClick={() => onSelect(workspace.id)}
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
                        {workspace.progress !== null ? (
                          <>
                            <strong>{workspace.progress}%</strong> course progress
                          </>
                        ) : (
                          <span style={{ color: '#94a3b8' }}>
                            No quiz activity yet
                          </span>
                        )}
                      </span>
                      <span className="progress-track" aria-hidden="true">
                        <span style={{ width: `${workspace.progress ?? 0}%` }} />
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

                <div className="workspace-card-actions">
                  {confirmingId === workspace.id ? (
                    <>
                      <button
                        className="workspace-danger-button"
                        type="button"
                        onClick={() => setConfirmingId(null)}
                        disabled={deletingId !== null}
                      >
                        Cancel
                      </button>
                      <button
                        className="workspace-danger-button is-danger"
                        type="button"
                        onClick={() => deleteWorkspace(workspace.id)}
                        disabled={deletingId !== null}
                      >
                        {deletingId === workspace.id
                          ? 'Deleting…'
                          : 'Delete permanently'}
                      </button>
                    </>
                  ) : (
                    <button
                      className="workspace-danger-button"
                      type="button"
                      onClick={() => setConfirmingId(workspace.id)}
                      disabled={deletingId !== null}
                      aria-label={`Delete ${workspace.name}`}
                    >
                      <Trash2 aria-hidden="true" />
                    </button>
                  )}
                </div>

                {confirmingId === workspace.id ? (
                  <p className="workspace-delete-warning">{deleteWarning}</p>
                ) : null}

                {deleteError?.id === workspace.id ? (
                  <p className="workspace-delete-error" role="alert">
                    {deleteError.message}
                  </p>
                ) : null}
              </div>
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

            {createError && (
              <div
                role="alert"
                style={{
                  margin: '16px 24px 0',
                  padding: '10px 14px',
                  borderRadius: '8px',
                  background: '#fef2f2',
                  border: '1px solid #fecaca',
                  color: '#991b1b',
                  fontSize: '13px',
                }}
              >
                {createError}
              </div>
            )}

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
                  Education level <small>Optional</small>
                </span>
                <select
                  value={draft.educationLevel}
                  onChange={(event) =>
                    updateDraft('educationLevel', event.target.value)
                  }
                >
                  {(
                    Object.keys(EDUCATION_LEVEL_LABELS) as EducationLevel[]
                  ).map((level) => (
                    <option key={level} value={level}>
                      {EDUCATION_LEVEL_LABELS[level]}
                    </option>
                  ))}
                </select>
              </label>

              <label className="form-field">
                <span>
                  Subject area <small>Optional</small>
                </span>
                <input
                  value={draft.subjectArea}
                  onChange={(event) =>
                    updateDraft('subjectArea', event.target.value)
                  }
                  placeholder="e.g. Biology"
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
