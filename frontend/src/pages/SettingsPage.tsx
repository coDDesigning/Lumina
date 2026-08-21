import { FormEvent, useCallback, useEffect, useState } from 'react'
import { BellRing, BrainCircuit, CheckCircle2, Settings, SlidersHorizontal } from 'lucide-react'
import PageLayout from '../components/PageLayout'
import { settingsAPI } from '../api/settings'
import { describeError } from '../api/errors'
import { LoadingSpinner } from '../components/LoadingSpinner'

const initialSettings = {
  studyMode: 'Exam',
  difficulty: 'Adaptive',
  questionCount: 10,
  summaryLength: 'Medium',
  detailLevel: 'Balanced',
  notifications: true,
  progressReminders: true,
}

type SettingsPageProps = {
  workspaceId?: string
}

function SettingsPage({ workspaceId }: SettingsPageProps) {
  const [preferences, setPreferences] = useState(initialSettings)
  const [isLoading, setIsLoading] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [statusMessage, setStatusMessage] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const courseId = workspaceId ? Number(workspaceId) : null

  const fetchSettings = useCallback(async (id: number) => {
    setIsLoading(true)
    setErrorMessage(null)
    try {
      const data = await settingsAPI.get(id)
      setPreferences({
        studyMode: data.study_mode,
        difficulty: data.difficulty,
        questionCount: data.question_count,
        summaryLength: data.summary_length,
        detailLevel: data.detail_level,
        notifications: data.notifications,
        progressReminders: data.progress_reminders,
      })
    } catch (err) {
      setErrorMessage(describeError(err, 'Failed to load workspace settings.').message)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (courseId && !isNaN(courseId)) {
      fetchSettings(courseId)
    }
  }, [courseId, fetchSettings])

  const updatePreference = <Key extends keyof typeof preferences>(
    field: Key,
    value: (typeof preferences)[Key],
  ) => {
    setPreferences((current) => ({ ...current, [field]: value }))
    setStatusMessage(null)
  }

  const savePreferences = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!courseId || isNaN(courseId)) {
      setStatusMessage('Preferences saved locally (select a specific workspace to sync).')
      return
    }

    setIsSaving(true)
    setErrorMessage(null)
    setStatusMessage(null)

    try {
      await settingsAPI.update(courseId, {
        study_mode: preferences.studyMode,
        difficulty: preferences.difficulty,
        question_count: preferences.questionCount,
        summary_length: preferences.summaryLength,
        detail_level: preferences.detailLevel,
        notifications: preferences.notifications,
        progress_reminders: preferences.progressReminders,
      })
      setStatusMessage('Workspace preferences saved successfully!')
    } catch (err) {
      setErrorMessage(describeError(err, 'Failed to save preferences.').message)
    } finally {
      setIsSaving(false)
    }
  }

  const resetPreferences = () => {
    setPreferences(initialSettings)
    setStatusMessage(null)
  }

  return (
    <PageLayout
      title="Settings"
      description="Shape the default study experience and generation preferences for your workspace."
      eyebrow="Workspace preferences"
      icon={Settings}
      workspaceId={workspaceId}
    >
      {isLoading ? (
        <div style={{ textAlign: 'center', padding: '48px' }}>
          <LoadingSpinner size="md" />
          <p style={{ marginTop: '12px', color: '#64748b' }}>Loading preferences…</p>
        </div>
      ) : (
        <form className="detail-form" onSubmit={savePreferences}>
          {errorMessage && (
            <div
              style={{
                padding: '12px 16px',
                background: '#fef2f2',
                border: '1px solid #fecaca',
                borderRadius: '10px',
                color: '#991b1b',
                marginBottom: '16px',
                fontSize: '14px',
              }}
              role="alert"
            >
              {errorMessage}
            </div>
          )}

          {statusMessage && (
            <div
              style={{
                padding: '12px 16px',
                background: '#ecfdf5',
                border: '1px solid #a7f3d0',
                borderRadius: '10px',
                color: '#065f46',
                marginBottom: '16px',
                fontSize: '14px',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
              }}
              role="status"
            >
              <CheckCircle2 size={18} />
              <span>{statusMessage}</span>
            </div>
          )}

          <div className="form-section-grid">
            <section className="form-section">
              <header className="form-section-header">
                <BrainCircuit aria-hidden="true" />
                <div>
                  <h2>Study defaults</h2>
                  <p>Choose how new learning sessions begin.</p>
                </div>
              </header>

              <div className="field-grid">
                <label className="form-field">
                  <span>Default mode</span>
                  <select
                    value={preferences.studyMode}
                    onChange={(event) =>
                      updatePreference('studyMode', event.target.value)
                    }
                  >
                    <option>Exam</option>
                    <option>Tutoring</option>
                    <option>Practice</option>
                  </select>
                </label>

                <label className="form-field">
                  <span>Quiz difficulty</span>
                  <select
                    value={preferences.difficulty}
                    onChange={(event) =>
                      updatePreference('difficulty', event.target.value)
                    }
                  >
                    <option>Adaptive</option>
                    <option>Easy</option>
                    <option>Medium</option>
                    <option>Hard</option>
                  </select>
                </label>

                <label className="form-field field-span-two">
                  <span>Questions per quiz</span>
                  <input
                    type="range"
                    min="5"
                    max="20"
                    step="5"
                    value={preferences.questionCount}
                    onChange={(event) =>
                      updatePreference('questionCount', Number(event.target.value))
                    }
                  />
                  <span className="range-caption">
                    {preferences.questionCount} questions
                  </span>
                </label>
              </div>
            </section>

            <section className="form-section">
              <header className="form-section-header">
                <SlidersHorizontal aria-hidden="true" />
                <div>
                  <h2>Generation style</h2>
                  <p>Set the preferred depth for generated material.</p>
                </div>
              </header>

              <div className="field-grid">
                <label className="form-field">
                  <span>Summary length</span>
                  <select
                    value={preferences.summaryLength}
                    onChange={(event) =>
                      updatePreference('summaryLength', event.target.value)
                    }
                  >
                    <option>Short</option>
                    <option>Medium</option>
                    <option>Long</option>
                  </select>
                </label>

                <label className="form-field">
                  <span>Detail level</span>
                  <select
                    value={preferences.detailLevel}
                    onChange={(event) =>
                      updatePreference('detailLevel', event.target.value)
                    }
                  >
                    <option>Concise</option>
                    <option>Balanced</option>
                    <option>Detailed</option>
                  </select>
                </label>
              </div>
            </section>

            <section className="form-section form-section-wide">
              <header className="form-section-header">
                <BellRing aria-hidden="true" />
                <div>
                  <h2>Notifications</h2>
                  <p>Control reminders and document status notifications.</p>
                </div>
              </header>

              <div className="toggle-list">
                <label className="toggle-row">
                  <span>
                    <strong>Document updates</strong>
                    <small>Show a notification when an uploaded source is ready.</small>
                  </span>
                  <input
                    type="checkbox"
                    checked={preferences.notifications}
                    onChange={(event) =>
                      updatePreference('notifications', event.target.checked)
                    }
                  />
                  <span className="toggle-control" aria-hidden="true" />
                </label>

                <label className="toggle-row">
                  <span>
                    <strong>Progress reminders</strong>
                    <small>Show a gentle reminder after an inactive study period.</small>
                  </span>
                  <input
                    type="checkbox"
                    checked={preferences.progressReminders}
                    onChange={(event) =>
                      updatePreference('progressReminders', event.target.checked)
                    }
                  />
                  <span className="toggle-control" aria-hidden="true" />
                </label>
              </div>
            </section>
          </div>

          <div className="form-footer">
            <p className="form-feedback" role="status">
              {courseId
                ? 'Preferences are synced with your workspace backend.'
                : 'Showing global default preferences.'}
            </p>
            <div className="form-actions">
              <button
                className="secondary-button"
                type="button"
                onClick={resetPreferences}
                disabled={isSaving}
              >
                Restore defaults
              </button>
              <button
                className="primary-button"
                type="submit"
                disabled={isSaving}
              >
                {isSaving ? 'Saving…' : 'Save preferences'}
              </button>
            </div>
          </div>
        </form>
      )}
    </PageLayout>
  )
}

export default SettingsPage
