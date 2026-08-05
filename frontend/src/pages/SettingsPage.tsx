import { FormEvent, useState } from 'react'
import { BellRing, BrainCircuit, Settings, SlidersHorizontal } from 'lucide-react'
import PageLayout from '../components/PageLayout'

const initialSettings = {
  studyMode: 'Exam',
  difficulty: 'Adaptive',
  questionCount: '10',
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
  const [saved, setSaved] = useState(false)

  const updatePreference = <Key extends keyof typeof preferences>(
    field: Key,
    value: (typeof preferences)[Key],
  ) => {
    setPreferences((current) => ({ ...current, [field]: value }))
    setSaved(false)
  }

  const savePreferences = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setSaved(true)
  }

  const resetPreferences = () => {
    setPreferences(initialSettings)
    setSaved(false)
  }

  return (
    <PageLayout
      title="Settings"
      description="Shape the default study experience without changing the content in your workspace."
      eyebrow="Workspace preferences"
      icon={Settings}
      workspaceId={workspaceId}
    >
      <form className="detail-form" onSubmit={savePreferences}>
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
                  max="25"
                  step="5"
                  value={preferences.questionCount}
                  onChange={(event) =>
                    updatePreference('questionCount', event.target.value)
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
                <p>Control the reminders shown during the demo flow.</p>
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
            {saved
              ? 'Preferences saved for this demo session.'
              : 'No backend connection is used for these preferences.'}
          </p>
          <div className="form-actions">
            <button
              className="secondary-button"
              type="button"
              onClick={resetPreferences}
            >
              Restore defaults
            </button>
            <button className="primary-button" type="submit">
              Save preferences
            </button>
          </div>
        </div>
      </form>
    </PageLayout>
  )
}

export default SettingsPage
