import { FormEvent, useState, useEffect, useCallback } from 'react'
import {
  BookOpen,
  Brain,
  CheckCircle2,
  Clock3,
  Coins,
  Cpu,
  Edit3,
  GraduationCap,
  LogOut,
  Plus,
  RefreshCw,
  Smile,
  Sparkles,
  Trash2,
  Trophy,
  X,
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import PageLayout from '../components/PageLayout'
import { useAuth } from '../context/AuthContext'
import { modelsAPI } from '../api/models'
import { profileKnowledgeAPI } from '../api/profileKnowledge'
import { userAPI } from '../api/user'
import type { AiModelInfo, ProfileKnowledgeItem } from '../api/types'

type ProfilePageProps = {
  workspaceId?: string
}

function ProfilePage({ workspaceId }: ProfilePageProps) {
  const { user, logout, refreshUser } = useAuth()
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

  // AI Models & Credits State
  const [models, setModels] = useState<AiModelInfo[]>([])
  const [loadingModels, setLoadingModels] = useState(true)
  const [selectedModel, setSelectedModel] = useState(user?.preferred_model || '')
  const [modelSaving, setModelSaving] = useState(false)
  const [modelSuccess, setModelSuccess] = useState<string | null>(null)
  const [modelError, setModelError] = useState<string | null>(null)
  const [refreshingCredits, setRefreshingCredits] = useState(false)

  // Profile Knowledge State
  const [knowledgeItems, setKnowledgeItems] = useState<ProfileKnowledgeItem[]>([])
  const [loadingKnowledge, setLoadingKnowledge] = useState(true)
  const [knowledgeError, setKnowledgeError] = useState<string | null>(null)
  const [knowledgeSuccess, setKnowledgeSuccess] = useState<string | null>(null)

  // Add / Edit Modal / Form State
  const [isAddingKnowledge, setIsAddingKnowledge] = useState(false)
  const [editingItemId, setEditingItemId] = useState<number | null>(null)
  const [formTopic, setFormTopic] = useState('')
  const [formDetail, setFormDetail] = useState('')
  const [submittingKnowledge, setSubmittingKnowledge] = useState(false)

  const fetchModels = useCallback(async () => {
    try {
      setLoadingModels(true)
      const available = await modelsAPI.list()
      setModels(available)
    } catch (err: unknown) {
      console.error('Failed to load AI models', err)
    } finally {
      setLoadingModels(false)
    }
  }, [])

  const fetchKnowledge = useCallback(async () => {
    try {
      setLoadingKnowledge(true)
      setKnowledgeError(null)
      const items = await profileKnowledgeAPI.list()
      setKnowledgeItems(items)
    } catch (err: unknown) {
      setKnowledgeError(
        err instanceof Error ? err.message : 'Failed to load profile knowledge.',
      )
    } finally {
      setLoadingKnowledge(false)
    }
  }, [])

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
      if (user.preferred_model) {
        setSelectedModel(user.preferred_model)
      }
    }
    fetchModels()
    fetchKnowledge()
  }, [user, fetchModels, fetchKnowledge])

  const handleModelChange = async (newModel: string) => {
    setSelectedModel(newModel)
    setModelSaving(true)
    setModelSuccess(null)
    setModelError(null)
    try {
      await userAPI.updatePreferredModel(newModel)
      await refreshUser()
      setModelSuccess(`Preferred AI model updated to ${newModel}`)
    } catch (err: unknown) {
      setModelError(
        err instanceof Error ? err.message : 'Failed to update preferred AI model',
      )
    } finally {
      setModelSaving(false)
    }
  }

  const handleRefreshCredits = async () => {
    setRefreshingCredits(true)
    try {
      await refreshUser()
    } finally {
      setRefreshingCredits(false)
    }
  }

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

  const handleOpenAdd = () => {
    setEditingItemId(null)
    setFormTopic('')
    setFormDetail('')
    setIsAddingKnowledge(true)
    setKnowledgeError(null)
    setKnowledgeSuccess(null)
  }

  const handleOpenEdit = (item: ProfileKnowledgeItem) => {
    setEditingItemId(item.id)
    setFormTopic(item.topic)
    setFormDetail(item.detail)
    setIsAddingKnowledge(true)
    setKnowledgeError(null)
    setKnowledgeSuccess(null)
  }

  const handleCancelKnowledge = () => {
    setIsAddingKnowledge(false)
    setEditingItemId(null)
    setFormTopic('')
    setFormDetail('')
  }

  const handleSaveKnowledge = async (event: FormEvent) => {
    event.preventDefault()
    if (!formTopic.trim() || !formDetail.trim()) return

    setSubmittingKnowledge(true)
    setKnowledgeError(null)
    setKnowledgeSuccess(null)

    try {
      if (editingItemId !== null) {
        const updated = await profileKnowledgeAPI.update(editingItemId, {
          topic: formTopic.trim(),
          detail: formDetail.trim(),
        })
        setKnowledgeItems((prev) =>
          prev.map((item) => (item.id === editingItemId ? updated : item)),
        )
        setKnowledgeSuccess('Knowledge topic updated successfully.')
      } else {
        const created = await profileKnowledgeAPI.create({
          topic: formTopic.trim(),
          detail: formDetail.trim(),
        })
        setKnowledgeItems((prev) => [created, ...prev])
        setKnowledgeSuccess('Knowledge topic added successfully.')
      }
      handleCancelKnowledge()
    } catch (err: unknown) {
      setKnowledgeError(
        err instanceof Error ? err.message : 'Failed to save knowledge entry.',
      )
    } finally {
      setSubmittingKnowledge(false)
    }
  }

  const handleDeleteKnowledge = async (id: number) => {
    setKnowledgeError(null)
    setKnowledgeSuccess(null)
    try {
      await profileKnowledgeAPI.delete(id)
      setKnowledgeItems((prev) => prev.filter((item) => item.id !== id))
      setKnowledgeSuccess('Knowledge topic removed.')
    } catch (err: unknown) {
      setKnowledgeError(
        err instanceof Error ? err.message : 'Failed to delete knowledge entry.',
      )
    }
  }

  const handleQuickImportSample = async () => {
    setSubmittingKnowledge(true)
    setKnowledgeError(null)
    setKnowledgeSuccess(null)
    try {
      const sampleItems = [
        {
          topic: 'Calculus & Linear Algebra',
          detail: 'Familiar with partial derivatives, gradient vectors, and matrix multiplication.',
        },
        {
          topic: 'Python Programming',
          detail: 'Comfortable with OOP, list comprehensions, and basic data structures.',
        },
      ]
      const imported = await profileKnowledgeAPI.importBulk({ items: sampleItems })
      setKnowledgeItems((prev) => [...imported, ...prev])
      setKnowledgeSuccess(`Successfully imported ${imported.length} sample knowledge topics.`)
    } catch (err: unknown) {
      setKnowledgeError(
        err instanceof Error ? err.message : 'Failed to import sample knowledge.',
      )
    } finally {
      setSubmittingKnowledge(false)
    }
  }

  const stats = [
    {
      label: 'AI Credits',
      value: user?.credits === null ? 'Unlimited' : `${user?.credits ?? 50}`,
      icon: Coins,
    },
    { label: 'Active courses', value: '4', icon: BookOpen },
    { label: 'Study sessions', value: '12', icon: Clock3 },
    { label: 'Quiz average', value: '82%', icon: Trophy },
  ]

  return (
    <PageLayout
      title="Profile"
      description="Manage your account profile, personal knowledge base, and learning settings."
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
            {stats.map(({ label, value, icon: Icon }) => (
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

        <section
          className="form-section profile-form-section"
          style={{ marginTop: '24px' }}
          aria-label="AI Model Preferences"
        >
          <header
            className="form-section-header"
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'flex-start',
            }}
          >
            <div style={{ display: 'flex', gap: '16px' }}>
              <Cpu
                aria-hidden="true"
                style={{ width: '24px', height: '24px', color: '#6366f1' }}
              />
              <div>
                <h2 style={{ fontSize: '18px', margin: '0 0 4px' }}>
                  AI Model Preferences & Credits
                </h2>
                <p style={{ margin: 0, color: '#6b7280', fontSize: '14px' }}>
                  Select your default model for Study Guides, Quizzes, Flashcards, AI Tutor, and Course Q&A.
                </p>
              </div>
            </div>
            <button
              type="button"
              className="secondary-button"
              onClick={handleRefreshCredits}
              disabled={refreshingCredits}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '6px',
                fontSize: '13px',
                padding: '6px 12px',
              }}
            >
              <RefreshCw
                style={{
                  width: '14px',
                  height: '14px',
                  animation: refreshingCredits ? 'spin 1s linear infinite' : 'none',
                }}
              />
              Refresh Credits
            </button>
          </header>

          <div
            style={{
              marginTop: '16px',
              display: 'flex',
              flexDirection: 'column',
              gap: '16px',
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '16px',
                padding: '12px 16px',
                background: '#f8fafc',
                borderRadius: '10px',
                border: '1px solid #e2e8f0',
              }}
            >
              <Coins
                style={{ width: '20px', height: '20px', color: '#f59e0b' }}
              />
              <div style={{ flex: 1 }}>
                <strong style={{ fontSize: '14px', color: '#1e293b' }}>
                  Available Balance:{' '}
                  {user?.credits === null ? (
                    <span style={{ color: '#10b981', fontWeight: 700 }}>
                      Unlimited (Admin)
                    </span>
                  ) : (
                    <span
                      style={{
                        color:
                          user?.credits && user.credits > 0
                            ? '#10b981'
                            : '#ef4444',
                        fontWeight: 700,
                      }}
                    >
                      {user?.credits ?? 0} Credits
                    </span>
                  )}
                </strong>
                <p
                  style={{
                    margin: '2px 0 0',
                    fontSize: '12px',
                    color: '#64748b',
                  }}
                >
                  Each AI generation deducts 1 credit. Regular accounts start with 50 credits.
                </p>
              </div>
            </div>

            <label className="form-field">
              <span>Preferred AI Model</span>
              <select
                value={selectedModel}
                onChange={(e) => handleModelChange(e.target.value)}
                disabled={loadingModels || modelSaving}
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  borderRadius: '8px',
                  border: '1px solid #cbd5e1',
                  background: '#ffffff',
                  fontSize: '14px',
                }}
              >
                {loadingModels ? (
                  <option value="">Loading available models...</option>
                ) : (
                  models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.display_name} {m.is_default ? '(Default)' : ''}
                    </option>
                  ))
                )}
              </select>
            </label>

            {modelSuccess && (
              <div
                role="status"
                style={{
                  padding: '8px 12px',
                  borderRadius: '8px',
                  background: '#f0fdf4',
                  border: '1px solid #bbf7d0',
                  color: '#166534',
                  fontSize: '13px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                }}
              >
                <CheckCircle2 style={{ width: '15px', height: '15px' }} />
                {modelSuccess}
              </div>
            )}

            {modelError && (
              <div
                role="alert"
                style={{
                  padding: '8px 12px',
                  borderRadius: '8px',
                  background: '#fef2f2',
                  border: '1px solid #fecaca',
                  color: '#991b1b',
                  fontSize: '13px',
                }}
              >
                {modelError}
              </div>
            )}
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

      {/* Profile Knowledge Section */}
      <section
        className="form-section profile-form-section"
        style={{ marginTop: '24px' }}
        aria-label="Profile Knowledge"
      >
        <header
          className="form-section-header"
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
          }}
        >
          <div style={{ display: 'flex', gap: '16px' }}>
            <Brain aria-hidden="true" style={{ width: '24px', height: '24px', color: '#6366f1' }} />
            <div>
              <h2 style={{ fontSize: '18px', margin: '0 0 4px' }}>
                Profile Knowledge & Learning Background
              </h2>
              <p style={{ margin: 0, color: '#6b7280', fontSize: '14px' }}>
                User-owned knowledge space that persists independently across courses. AI tutors use these topics as supplementary background context.
              </p>
            </div>
          </div>
          <div style={{ display: 'flex', gap: '8px' }}>
            {knowledgeItems.length === 0 && (
              <button
                type="button"
                className="secondary-button"
                onClick={handleQuickImportSample}
                disabled={submittingKnowledge}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '6px',
                  fontSize: '13px',
                  padding: '6px 12px',
                }}
              >
                <Sparkles style={{ width: '14px', height: '14px' }} />
                Import Samples
              </button>
            )}
            <button
              type="button"
              className="primary-button"
              onClick={handleOpenAdd}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '6px',
                fontSize: '13px',
                padding: '6px 14px',
              }}
            >
              <Plus style={{ width: '14px', height: '14px' }} />
              Add Knowledge Topic
            </button>
          </div>
        </header>

        {knowledgeFeedbackBanner(knowledgeSuccess, knowledgeError)}

        {/* Modal / Inline Add/Edit Form */}
        {isAddingKnowledge && (
          <form
            onSubmit={handleSaveKnowledge}
            style={{
              marginTop: '16px',
              padding: '18px',
              background: '#f8fafc',
              border: '1px solid #cbd5e1',
              borderRadius: '12px',
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: '12px',
              }}
            >
              <strong style={{ fontSize: '15px', color: '#1e293b' }}>
                {editingItemId !== null ? 'Edit Knowledge Topic' : 'New Knowledge Topic'}
              </strong>
              <button
                type="button"
                onClick={handleCancelKnowledge}
                style={{
                  background: 'none',
                  border: 'none',
                  cursor: 'pointer',
                  color: '#64748b',
                }}
                aria-label="Close form"
              >
                <X style={{ width: '18px', height: '18px' }} />
              </button>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <label className="form-field">
                <span>Topic Name</span>
                <input
                  type="text"
                  placeholder="e.g., Linear Algebra, Discrete Math, React Hooks"
                  value={formTopic}
                  onChange={(e) => setFormTopic(e.target.value)}
                  maxLength={200}
                  required
                />
              </label>

              <label className="form-field">
                <span>Knowledge Details & Background</span>
                <textarea
                  placeholder="Describe your current mastery level, concepts you already know, or topics you want to build on..."
                  value={formDetail}
                  onChange={(e) => setFormDetail(e.target.value)}
                  rows={3}
                  style={{
                    width: '100%',
                    padding: '10px 12px',
                    borderRadius: '8px',
                    border: '1px solid #cbd5e1',
                    fontFamily: 'inherit',
                    fontSize: '14px',
                    resize: 'vertical',
                  }}
                  required
                />
              </label>

              <div
                style={{
                  display: 'flex',
                  justifyContent: 'flex-end',
                  gap: '8px',
                  marginTop: '6px',
                }}
              >
                <button
                  type="button"
                  className="secondary-button"
                  onClick={handleCancelKnowledge}
                  disabled={submittingKnowledge}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="primary-button"
                  disabled={submittingKnowledge || !formTopic.trim() || !formDetail.trim()}
                >
                  {submittingKnowledge
                    ? 'Saving...'
                    : editingItemId !== null
                      ? 'Update Topic'
                      : 'Save Topic'}
                </button>
              </div>
            </div>
          </form>
        )}

        {/* Knowledge Items List */}
        <div style={{ marginTop: '16px' }}>
          {loadingKnowledge ? (
            <p style={{ color: '#64748b', fontSize: '14px', textAlign: 'center', padding: '24px 0' }}>
              Loading profile knowledge...
            </p>
          ) : knowledgeItems.length === 0 ? (
            <div
              style={{
                textAlign: 'center',
                padding: '36px 16px',
                border: '2px dashed #e2e8f0',
                borderRadius: '12px',
                background: '#fafafa',
              }}
            >
              <Brain
                style={{
                  width: '36px',
                  height: '36px',
                  color: '#94a3b8',
                  margin: '0 auto 8px',
                }}
              />
              <h3 style={{ fontSize: '16px', margin: '0 0 6px', color: '#334155' }}>
                No knowledge topics added yet
              </h3>
              <p
                style={{
                  fontSize: '13px',
                  color: '#64748b',
                  maxWidth: '450px',
                  margin: '0 auto 14px',
                }}
              >
                Add topics and skills you are familiar with. They will persist even if courses are deleted and help AI tutors tailor explanations to your background.
              </p>
              <button
                type="button"
                className="secondary-button"
                onClick={handleOpenAdd}
                style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}
              >
                <Plus style={{ width: '14px', height: '14px' }} />
                Add Your First Topic
              </button>
            </div>
          ) : (
            <div
              style={{
                display: 'grid',
                gap: '12px',
                gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
              }}
            >
              {knowledgeItems.map((item) => (
                <article
                  key={item.id}
                  style={{
                    padding: '16px',
                    borderRadius: '10px',
                    border: '1px solid #e2e8f0',
                    background: '#ffffff',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
                    display: 'flex',
                    flexDirection: 'column',
                    justifyContent: 'space-between',
                  }}
                >
                  <div>
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'flex-start',
                        marginBottom: '8px',
                      }}
                    >
                      <h4
                        style={{
                          margin: 0,
                          fontSize: '15px',
                          fontWeight: 600,
                          color: '#1e293b',
                        }}
                      >
                        {item.topic}
                      </h4>
                      <div style={{ display: 'flex', gap: '4px' }}>
                        <button
                          type="button"
                          onClick={() => handleOpenEdit(item)}
                          aria-label={`Edit ${item.topic}`}
                          style={{
                            background: 'none',
                            border: 'none',
                            cursor: 'pointer',
                            color: '#64748b',
                            padding: '4px',
                            borderRadius: '4px',
                          }}
                        >
                          <Edit3 style={{ width: '14px', height: '14px' }} />
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDeleteKnowledge(item.id)}
                          aria-label={`Delete ${item.topic}`}
                          style={{
                            background: 'none',
                            border: 'none',
                            cursor: 'pointer',
                            color: '#e11d48',
                            padding: '4px',
                            borderRadius: '4px',
                          }}
                        >
                          <Trash2 style={{ width: '14px', height: '14px' }} />
                        </button>
                      </div>
                    </div>
                    <p
                      style={{
                        margin: 0,
                        fontSize: '13px',
                        color: '#475569',
                        lineHeight: 1.5,
                        whiteSpace: 'pre-wrap',
                      }}
                    >
                      {item.detail}
                    </p>
                  </div>
                  <div
                    style={{
                      marginTop: '12px',
                      paddingTop: '8px',
                      borderTop: '1px solid #f1f5f9',
                      fontSize: '11px',
                      color: '#94a3b8',
                    }}
                  >
                    Updated {new Date(item.updated_at).toLocaleDateString()}
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>
      </section>
    </PageLayout>
  )
}

function knowledgeFeedbackBanner(success: string | null, error: string | null) {
  if (success) {
    return (
      <div
        role="status"
        style={{
          marginTop: '12px',
          padding: '8px 12px',
          borderRadius: '8px',
          background: '#f0fdf4',
          border: '1px solid #bbf7d0',
          color: '#166534',
          fontSize: '13px',
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
        }}
      >
        <CheckCircle2 style={{ width: '15px', height: '15px' }} />
        {success}
      </div>
    )
  }
  if (error) {
    return (
      <div
        role="alert"
        style={{
          marginTop: '12px',
          padding: '8px 12px',
          borderRadius: '8px',
          background: '#fef2f2',
          border: '1px solid #fecaca',
          color: '#991b1b',
          fontSize: '13px',
        }}
      >
        {error}
      </div>
    )
  }
  return null
}

export default ProfilePage
