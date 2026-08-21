import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ProfilePage from './ProfilePage'
import { modelsAPI } from '../api/models'
import { profileKnowledgeAPI } from '../api/profileKnowledge'
import { userAPI } from '../api/user'

// These suites are not about credits; an unmetered account renders no credit UI.
vi.mock('../context/CreditContext', () => ({
  useCredits: () => ({
    status: null,
    isLoading: false,
    error: null,
    refresh: vi.fn(),
    isMetered: false,
    costOf: () => null,
    canAfford: () => true,
  }),
}))


const mockLogout = vi.fn()
const mockRefreshUser = vi.fn()

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 1,
      name: 'Ada Lovelace',
      email: 'ada@example.com',
      role: 'Student',
      is_banned: false,
      credits: 42,
      preferred_model: 'gemini-1.5-flash',
      education_level: 'unspecified',
    },
    isAuthenticated: true,
    isLoading: false,
    login: vi.fn(),
    logout: mockLogout,
    refreshUser: mockRefreshUser,
  }),
}))

vi.mock('../api/models', () => ({
  modelsAPI: {
    list: vi.fn(),
  },
}))

vi.mock('../api/profileKnowledge', () => ({
  profileKnowledgeAPI: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    importBulk: vi.fn(),
  },
}))

vi.mock('../api/user', () => ({
  userAPI: {
    updatePreferredModel: vi.fn(),
    getCreditTransactions: vi.fn(),
  },
}))

const mockModelsList = vi.mocked(modelsAPI.list)
const mockKnowledgeList = vi.mocked(profileKnowledgeAPI.list)
const mockKnowledgeCreate = vi.mocked(profileKnowledgeAPI.create)
const mockKnowledgeDelete = vi.mocked(profileKnowledgeAPI.delete)
const mockUpdatePreferredModel = vi.mocked(userAPI.updatePreferredModel)
const mockGetCreditTransactions = vi.mocked(userAPI.getCreditTransactions)

function renderProfilePage() {
  return render(
    <MemoryRouter initialEntries={['/profile']}>
      <ProfilePage />
    </MemoryRouter>,
  )
}

describe('ProfilePage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockModelsList.mockResolvedValue([
      {
        id: 'gemini-1.5-flash',
        model: 'gemini-1.5-flash',
        display_name: 'Gemini 1.5 Flash',
        provider: 'gemini',
        is_default: true,
      },
      {
        id: 'gpt-4o-mini',
        model: 'gpt-4o-mini',
        display_name: 'GPT-4o Mini',
        provider: 'openai',
        is_default: false,
      },
    ])
    mockKnowledgeList.mockResolvedValue([
      {
        id: 1,
        user_id: 1,
        topic: 'Linear Algebra',
        detail: 'Eigenvalues and matrix decomposition.',
        created_at: '2026-08-20T10:00:00Z',
        updated_at: '2026-08-20T10:00:00Z',
      },
    ])
    mockGetCreditTransactions.mockResolvedValue([])
  })

  it('renders real user info and excludes fake personal info form controls and fake save button', async () => {
    renderProfilePage()

    // Real user info is visible
    expect(await screen.findByText('Ada Lovelace')).toBeInTheDocument()
    expect(screen.getByText('ada@example.com')).toBeInTheDocument()
    expect(screen.getByText('Student')).toBeInTheDocument()
    expect(screen.getByText('42')).toBeInTheDocument()

    // Fake form elements must NOT be in the document
    expect(screen.queryByLabelText('First name')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Last name')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Institution')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Department')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save profile' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reset' })).not.toBeInTheDocument()
    expect(screen.queryByText('Bilkent University')).not.toBeInTheDocument()
  })

  it('allows changing preferred AI model and calls API', async () => {
    const user = userEvent.setup()
    mockUpdatePreferredModel.mockResolvedValue({
      id: 1,
      name: 'Ada Lovelace',
      email: 'ada@example.com',
      role: 'Student',
      is_banned: false,
      credits: 42,
      preferred_model: 'gpt-4o-mini',
      education_level: 'unspecified',
    })

    renderProfilePage()

    const select = await screen.findByLabelText('Preferred AI Model')
    await user.selectOptions(select, 'gpt-4o-mini')

    expect(mockUpdatePreferredModel).toHaveBeenCalledWith('gpt-4o-mini')
    expect(
      await screen.findByText('Preferred AI model updated to gpt-4o-mini'),
    ).toBeInTheDocument()
  })

  it('lists knowledge items and allows adding a new topic', async () => {
    const user = userEvent.setup()
    mockKnowledgeCreate.mockResolvedValue({
      id: 2,
      user_id: 1,
      topic: 'Quantum Computing',
      detail: 'Qubits, superposition, and entanglement.',
      created_at: '2026-08-21T10:00:00Z',
      updated_at: '2026-08-21T10:00:00Z',
    })

    renderProfilePage()

    expect(await screen.findByText('Linear Algebra')).toBeInTheDocument()
    expect(
      screen.getByText('Eigenvalues and matrix decomposition.'),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Add Knowledge Topic' }))

    const topicInput = await screen.findByLabelText('Topic Name')
    const detailInput = screen.getByLabelText('Knowledge Details & Background')

    await user.type(topicInput, 'Quantum Computing')
    await user.type(detailInput, 'Qubits, superposition, and entanglement.')

    await user.click(screen.getByRole('button', { name: 'Save Topic' }))

    await waitFor(() => {
      expect(mockKnowledgeCreate).toHaveBeenCalledWith({
        topic: 'Quantum Computing',
        detail: 'Qubits, superposition, and entanglement.',
      })
    })

    expect(await screen.findByText('Quantum Computing')).toBeInTheDocument()
    expect(
      screen.getByText('Knowledge topic added successfully.'),
    ).toBeInTheDocument()
  })

  it('allows deleting a knowledge topic', async () => {
    const user = userEvent.setup()
    mockKnowledgeDelete.mockResolvedValue(undefined)

    renderProfilePage()

    expect(await screen.findByText('Linear Algebra')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Delete Linear Algebra' }))

    await waitFor(() => {
      expect(mockKnowledgeDelete).toHaveBeenCalledWith(1)
    })

    await waitFor(() => {
      expect(screen.queryByText('Eigenvalues and matrix decomposition.')).not.toBeInTheDocument()
    })
    expect(screen.getByText('Knowledge topic removed.')).toBeInTheDocument()
  })

  it('confirms profile knowledge is structured-only and contains no file or document upload controls', async () => {
    renderProfilePage()

    expect(await screen.findByText('Profile Knowledge & Learning Background')).toBeInTheDocument()
    expect(screen.queryByLabelText(/upload/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/upload document/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/drag and drop/i)).not.toBeInTheDocument()
    const fileInputs = document.querySelectorAll('input[type="file"]')
    expect(fileInputs.length).toBe(0)
  })
})
