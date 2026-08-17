import { FormEvent, useState, useEffect } from 'react'
import { BookOpen, Clock3, GraduationCap, LogOut, Smile, Trophy } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import PageLayout from '../components/PageLayout'
import { useAuth } from '../context/AuthContext'

const profileStats = [
  { label: 'Active courses', value: '4', icon: BookOpen },
  { label: 'Study sessions', value: '12', icon: Clock3 },
  { label: 'Quiz average', value: '82%', icon: Trophy },
]

type ProfilePageProps = {
  workspaceId?: string
}

function ProfilePage({ workspaceId }: ProfilePageProps) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  const nameParts = user?.name ? user.name.split(' ') : ['Lumina', 'Learner']
  const firstName = nameParts[0] || 'Lumina'
  const lastName = nameParts.slice(1).join(' ') || 'Learner'

  const [profile, setProfile] = useState({
    firstName,
    lastName,
    email: user?.email || 'learner@lumina.ai',
    institution: 'Bilkent University',
    department: 'Computer Science',
    role: user?.role || 'Student',
  })
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (user) {
      const parts = user.name ? user.name.split(' ') : ['Lumina', 'Learner']
      setProfile((prev) => ({
        ...prev,
        firstName: parts[0] || 'Lumina',
        lastName: parts.slice(1).join(' ') || 'Learner',
        email: user.email,
        role: user.role || 'Student',
      }))
    }
  }, [user])

  const updateProfile = (field: keyof typeof profile, value: string) => {
    setProfile((current) => ({ ...current, [field]: value }))
    setSaved(false)
  }

  const saveProfile = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setSaved(true)
  }

  const resetProfile = () => {
    if (user) {
      const parts = user.name ? user.name.split(' ') : ['Lumina', 'Learner']
      setProfile({
        firstName: parts[0] || 'Lumina',
        lastName: parts.slice(1).join(' ') || 'Learner',
        email: user.email,
        institution: 'Bilkent University',
        department: 'Computer Science',
        role: user.role || 'Student',
      })
    }
    setSaved(false)
  }

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  return (
    <PageLayout
      title="Profile"
      description="Manage your account profile and learning settings."
      eyebrow="Account & Preferences"
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
              <p>Account details associated with your Lumina session.</p>
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
            </label>
          </div>
        </section>

        <div className="form-footer">
          <p className="form-feedback" role="status">
            {saved
              ? 'Profile saved successfully.'
              : `Logged in as ${profile.email}`}
          </p>
          <div className="form-actions">
            <button
              className="secondary-button"
              type="button"
              onClick={resetProfile}
            >
              Reset
            </button>
            <button
              type="button"
              onClick={handleLogout}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '6px',
                padding: '9px 16px',
                borderRadius: '8px',
                border: '1px solid #fda4af',
                background: '#fff1f2',
                color: '#e11d48',
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              <LogOut style={{ width: '16px', height: '16px' }} />
              Log Out
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
