import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { CreditStatus } from '@/api/types'
import { ThemeProvider } from '@/app/ThemeProvider'
import AccountPage from './AccountPage'
import { modelsAPI } from '@/api/models'
import { profileKnowledgeAPI } from '@/api/profileKnowledge'
import { userAPI } from '@/api/user'

// These suites are not about credits; an unmetered account renders no credit UI.
const creditState: { status: CreditStatus | null } = { status: null }

vi.mock('@/context/CreditContext', () => ({
  useCredits: () => ({
    status: creditState.status,
    isLoading: false,
    error: null,
    refresh: vi.fn(),
    isMetered: creditState.status != null && creditState.status.credits !== null,
    costOf: (source: string) => creditState.status?.generation_costs?.[source] ?? null,
    canAfford: () => true,
  }),
}))


const mockLogout = vi.fn()
const mockRefreshUser = vi.fn()

vi.mock('@/context/AuthContext', () => ({
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

vi.mock('@/api/models', () => ({
  modelsAPI: {
    list: vi.fn(),
  },
}))

vi.mock('@/api/profileKnowledge', () => ({
  profileKnowledgeAPI: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    importBulk: vi.fn(),
  },
}))

vi.mock('@/api/user', () => ({
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

function renderAccountPage() {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={['/profile']}>
        <AccountPage />
      </MemoryRouter>
    </ThemeProvider>,
  )
}

describe('AccountPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockModelsList.mockResolvedValue([
      {
        id: 'gemini-1.5-flash',
        model: 'gemini-1.5-flash',
        display_name: 'Gemini 1.5 Flash',
        provider: 'gemini',
        is_default: true,
        cost_hint: 'Metered (1-2 credits)',
        capabilities: ['study_guide', 'quiz'],
        description: 'Fast Google Gemini model',
        is_local: false,
        supports_json: true,
      },
      {
        id: 'gpt-4o-mini',
        model: 'gpt-4o-mini',
        display_name: 'GPT-4o Mini',
        provider: 'openai',
        is_default: false,
        cost_hint: 'Metered (1 credit)',
        capabilities: ['study_guide', 'quiz', 'flashcard'],
        description: 'Compact OpenAI model',
        is_local: false,
        supports_json: true,
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
    creditState.status = null
  })

  it('renders real user info and excludes fake personal info form controls and fake save button', async () => {
    renderAccountPage()

    // Real user info is visible
    expect(await screen.findByText('Ada Lovelace')).toBeInTheDocument()
    expect(screen.getByText('ada@example.com')).toBeInTheDocument()
    expect(screen.getByText('Student')).toBeInTheDocument()
    // The balance is not read from the auth snapshot, and this mock is unmetered,
    // so no credit figure may appear here at all.
    expect(screen.queryByText('42')).not.toBeInTheDocument()
    expect(screen.getByText('This account is not metered')).toBeInTheDocument()

    // Fake form elements must NOT be in the document
    expect(screen.queryByLabelText('First name')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Last name')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Institution')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Department')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save profile' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reset' })).not.toBeInTheDocument()
    expect(screen.queryByText('Bilkent University')).not.toBeInTheDocument()
  })

  it('renders model capabilities and cost hints for the selected model', async () => {
    renderAccountPage()

    expect(await screen.findByTestId('model-details-card')).toBeInTheDocument()
    expect(screen.getByText('Metered (1-2 credits)')).toBeInTheDocument()
    expect(screen.getByText('Fast Google Gemini model')).toBeInTheDocument()
    expect(screen.getByTestId('model-capability-study_guide')).toBeInTheDocument()
    expect(screen.getByTestId('model-capability-quiz')).toBeInTheDocument()
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

    renderAccountPage()

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

    renderAccountPage()

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

    renderAccountPage()

    expect(await screen.findByText('Linear Algebra')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Delete Linear Algebra' }))
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Remove' }),
    )

    await waitFor(() => {
      expect(mockKnowledgeDelete).toHaveBeenCalledWith(1)
    })

    await waitFor(() => {
      expect(screen.queryByText('Eigenvalues and matrix decomposition.')).not.toBeInTheDocument()
    })
    expect(screen.getByText('Knowledge topic removed.')).toBeInTheDocument()
  })

  it('confirms profile knowledge is structured-only and contains no file or document upload controls', async () => {
    renderAccountPage()

    expect(await screen.findByText('Profile Knowledge & Learning Background')).toBeInTheDocument()
    expect(screen.queryByLabelText(/upload/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/upload document/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/drag and drop/i)).not.toBeInTheDocument()
    const fileInputs = document.querySelectorAll('input[type="file"]')
    expect(fileInputs.length).toBe(0)
  })
})

describe('AccountPage credits', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockModelsList.mockResolvedValue([])
    mockKnowledgeList.mockResolvedValue([])
    mockGetCreditTransactions.mockResolvedValue([])
  })

  it('shows the balance the credits endpoint reports, not the auth snapshot', async () => {
    creditState.status = {
      credits: 7,
      metering_enabled: true,
      monthly_grant: 20,
      balance_cap: 40,
      next_grant_at: '2026-12-01T00:00:00Z',
      generation_costs: { study_guide: 1, quiz: 1, quiz_open_ended: 2 },
    }

    renderAccountPage()

    // The auth mock says 42; the credits endpoint says 7. The endpoint wins.
    expect(await screen.findByText('7')).toBeInTheDocument()
    expect(screen.queryByText('42')).not.toBeInTheDocument()
  })

  it('prices a quiz with written questions higher, from the served cost table', async () => {
    creditState.status = {
      credits: 7,
      metering_enabled: true,
      monthly_grant: 20,
      balance_cap: 40,
      next_grant_at: '2026-12-01T00:00:00Z',
      generation_costs: { study_guide: 1, quiz: 1, quiz_open_ended: 2 },
    }

    renderAccountPage()

    expect(await screen.findByText('Quiz including written questions')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  it('renders no credit UI whatsoever for an unmetered account', async () => {
    creditState.status = null

    renderAccountPage()

    expect(await screen.findByText('This account is not metered')).toBeInTheDocument()
    expect(screen.queryByText('Credits left')).not.toBeInTheDocument()
    expect(screen.queryByText('What things cost')).not.toBeInTheDocument()
  })
})
