import { FormEvent, useState } from 'react'
import { BookOpen, Clock3, GraduationCap, Smile, Trophy } from 'lucide-react'
import PageLayout from '../components/PageLayout'

const initialProfile = {
  firstName: 'Alex',
  lastName: 'Morgan',
  email: 'alex.morgan@example.com',
  institution: 'Lumina University',
  department: 'Behavioral Sciences',
  role: 'Student',
}

const profileStats = [
  { label: 'Active courses', value: '4', icon: BookOpen },
  { label: 'Study sessions', value: '12', icon: Clock3 },
  { label: 'Quiz average', value: '82%', icon: Trophy },
]

type ProfilePageProps = {
  workspaceId?: string
}

function ProfilePage({ workspaceId }: ProfilePageProps) {
  const [profile, setProfile] = useState(initialProfile)
  const [saved, setSaved] = useState(false)

  const updateProfile = (field: keyof typeof profile, value: string) => {
    setProfile((current) => ({ ...current, [field]: value }))
    setSaved(false)
  }

  const saveProfile = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setSaved(true)
  }

  const resetProfile = () => {
    setProfile(initialProfile)
    setSaved(false)
  }

  return (
    <PageLayout
      title="Profile"
      description="Manage the learner identity and academic context presented in the Lumina demo."
      eyebrow="Personal workspace"
      icon={Smile}
      workspaceId={workspaceId}
    >
      <form className="detail-form" onSubmit={saveProfile}>
        <section className="profile-overview" aria-label="Profile overview">
          <div className="profile-identity">
            <span className="profile-avatar" aria-hidden="true">
              {profile.firstName.charAt(0)}
              {profile.lastName.charAt(0)}
            </span>
            <div>
              <h2>
                {profile.firstName} {profile.lastName}
              </h2>
              <p>{profile.email}</p>
              <span className="role-badge">{profile.role}</span>
            </div>
          </div>

          <div className="profile-stats">
            {profileStats.map(({ label, value, icon: Icon }) => (
              <article key={label}>
                <Icon aria-hidden="true" />
                <div>
                  <strong>{value}</strong>
                  <span>{label}</span>
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="form-section profile-form-section">
          <header className="form-section-header">
            <GraduationCap aria-hidden="true" />
            <div>
              <h2>Personal information</h2>
              <p>Details used to personalize the frontend demo.</p>
            </div>
          </header>

          <div className="field-grid">
            <label className="form-field">
              <span>First name</span>
              <input
                value={profile.firstName}
                onChange={(event) =>
                  updateProfile('firstName', event.target.value)
                }
                required
              />
            </label>

            <label className="form-field">
              <span>Last name</span>
              <input
                value={profile.lastName}
                onChange={(event) =>
                  updateProfile('lastName', event.target.value)
                }
                required
              />
            </label>

            <label className="form-field field-span-two">
              <span>Email address</span>
              <input
                type="email"
                value={profile.email}
                onChange={(event) => updateProfile('email', event.target.value)}
                required
              />
            </label>

            <label className="form-field">
              <span>Institution</span>
              <input
                value={profile.institution}
                onChange={(event) =>
                  updateProfile('institution', event.target.value)
                }
              />
            </label>

            <label className="form-field">
              <span>Department</span>
              <input
                value={profile.department}
                onChange={(event) =>
                  updateProfile('department', event.target.value)
                }
              />
            </label>

            <label className="form-field field-span-two">
              <span>Role</span>
              <input value={profile.role} readOnly aria-readonly="true" />
              <small>Roles are managed by the future authentication service.</small>
            </label>
          </div>
        </section>

        <div className="form-footer">
          <p className="form-feedback" role="status">
            {saved
              ? 'Profile saved for this demo session.'
              : 'Profile changes remain local to the frontend.'}
          </p>
          <div className="form-actions">
            <button
              className="secondary-button"
              type="button"
              onClick={resetProfile}
            >
              Reset
            </button>
            <button className="primary-button" type="submit">
              Save profile
            </button>
          </div>
        </div>
      </form>
    </PageLayout>
  )
}

export default ProfilePage
