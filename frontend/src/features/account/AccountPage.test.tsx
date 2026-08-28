import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { APIError } from '@/api/client'
import type { CreditStatus } from '@/api/types'
import { ThemeProvider } from '@/app/ThemeProvider'
import AccountAppearancePage from './AccountAppearancePage'
import AccountLayout from './AccountLayout'
import AccountYouPage from './AccountYouPage'
import { AiPreferencesSection } from './AiPreferencesSection'
import { ProfileKnowledgeSection } from './ProfileKnowledgeSection'
import { modelsAPI } from '@/api/models'
import { profileDocumentsAPI } from '@/api/profileDocuments'
import { profileKnowledgeAPI } from '@/api/profileKnowledge'
import { userAPI } from '@/api/user'
import { adsAPI } from '@/api/ads'

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
      is_email_verified: true,
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

vi.mock('@/api/profileDocuments', () => ({
  profileDocumentsAPI: {
    list: vi.fn(),
    getStatus: vi.fn(),
    upload: vi.fn(),
    retry: vi.fn(),
    delete: vi.fn(),
  },
}))

vi.mock('@/api/user', () => ({
  userAPI: {
    updatePreferredModel: vi.fn(),
    updateEducationLevel: vi.fn(),
    getCreditTransactions: vi.fn(),
  },
}))

vi.mock('@/api/ads', () => ({
  adsAPI: {
    getConfig: vi.fn(),
    recordTelemetry: vi.fn(),
  },
}))

const mockModelsList = vi.mocked(modelsAPI.list)
const mockKnowledgeList = vi.mocked(profileKnowledgeAPI.list)
const mockKnowledgeCreate = vi.mocked(profileKnowledgeAPI.create)
const mockKnowledgeDelete = vi.mocked(profileKnowledgeAPI.delete)
const mockProfileDocumentsList = vi.mocked(profileDocumentsAPI.list)
const mockProfileDocumentsDelete = vi.mocked(profileDocumentsAPI.delete)
const mockAdsGetConfig = vi.mocked(adsAPI.getConfig)
const mockUpdatePreferredModel = vi.mocked(userAPI.updatePreferredModel)
const mockUpdateEducationLevel = vi.mocked(userAPI.updateEducationLevel)
const mockGetCreditTransactions = vi.mocked(userAPI.getCreditTransactions)

function renderAccountPage(path = '/account') {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/account" element={<AccountLayout />}>
            <Route index element={<AccountYouPage />} />
            <Route path="background" element={<ProfileKnowledgeSection />} />
            <Route path="ai" element={<AiPreferencesSection />} />
            <Route path="appearance" element={<AccountAppearancePage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  )
}

describe('AccountPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockAdsGetConfig.mockResolvedValue({
      enabled: false,
      provider: null,
      publisher_id: null,
    })
    mockProfileDocumentsList.mockResolvedValue([])
    mockKnowledgeList.mockResolvedValue([])
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

    expect(await screen.findByText('Ada Lovelace')).toBeInTheDocument()
    expect(screen.getByText('ada@example.com')).toBeInTheDocument()
    expect(screen.getByText('Student')).toBeInTheDocument()
    expect(screen.queryByText('42')).not.toBeInTheDocument()

    expect(screen.queryByLabelText('First name')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Last name')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Institution')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Department')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save profile' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reset' })).not.toBeInTheDocument()
    expect(screen.queryByText('Bilkent University')).not.toBeInTheDocument()
  })

  it('renders model capabilities and cost hints for the selected model', async () => {
    renderAccountPage('/account/ai')

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
      is_email_verified: true,
      credits: 42,
      preferred_model: 'gpt-4o-mini',
      education_level: 'unspecified',
    })

    renderAccountPage('/account/ai')

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

    renderAccountPage('/account/background')

    expect(await screen.findByText('Linear Algebra')).toBeInTheDocument()
    expect(
      screen.getByText('Eigenvalues and matrix decomposition.'),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Add a note' }))

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

    renderAccountPage('/account/background')

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

  it('confirms profile knowledge includes background document upload controls', async () => {
    renderAccountPage('/account/background')

    expect(await screen.findByRole('heading', { name: 'Your background' })).toBeInTheDocument()
    expect(
      screen.getByText(/These notes belong to you, not to any course/),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /upload document/i })).toBeInTheDocument()
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
      email_verification_required: false,
      is_email_verified: true,
      monthly_grant: 20,
      balance_cap: 40,
      next_grant_at: '2026-12-01T00:00:00Z',
      generation_costs: { study_guide: 1, quiz: 1, quiz_open_ended: 2 },
    }

    renderAccountPage('/account/ai')

    expect(await screen.findByText('7')).toBeInTheDocument()
    expect(screen.queryByText('42')).not.toBeInTheDocument()
  })

  it('prices a quiz with written questions higher, from the served cost table', async () => {
    creditState.status = {
      credits: 7,
      metering_enabled: true,
      email_verification_required: false,
      is_email_verified: true,
      monthly_grant: 20,
      balance_cap: 40,
      next_grant_at: '2026-12-01T00:00:00Z',
      generation_costs: { study_guide: 1, quiz: 1, quiz_open_ended: 2 },
    }

    renderAccountPage('/account/ai')

    expect(await screen.findByText('Quiz including written questions')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
  })

  it('renders no credit UI whatsoever for an unmetered account', async () => {
    creditState.status = null

    renderAccountPage('/account/ai')

    expect(await screen.findByText('This account is not metered')).toBeInTheDocument()
    expect(screen.queryByText('Credits left')).not.toBeInTheDocument()
    expect(screen.queryByText('What things cost')).not.toBeInTheDocument()
    expect(screen.queryByText(/credits/i)).toBeNull()
  })

  it('gives every part of the account its own address', async () => {
    renderAccountPage()

    const nav = screen.getByRole('navigation', { name: 'Account sections' })
    expect(within(nav).getByRole('link', { name: 'You' })).toHaveAttribute('href', '/account')
    expect(within(nav).getByRole('link', { name: 'Your background' })).toHaveAttribute(
      'href',
      '/account/background',
    )
    expect(within(nav).getByRole('link', { name: 'AI' })).toHaveAttribute('href', '/account/ai')
    expect(within(nav).getByRole('link', { name: 'Appearance' })).toHaveAttribute(
      'href',
      '/account/appearance',
    )
  })

  it('marks the section being read as the current one', async () => {
    renderAccountPage('/account/background')

    const nav = screen.getByRole('navigation', { name: 'Account sections' })
    expect(within(nav).getByRole('link', { name: 'Your background' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(within(nav).getByRole('link', { name: 'You' })).not.toHaveAttribute('aria-current')
  })

  it('keeps who you are visible from every section', async () => {
    renderAccountPage('/account/appearance')

    expect(await screen.findByText('Ada Lovelace')).toBeInTheDocument()
    expect(screen.getByLabelText('Theme')).toBeInTheDocument()
  })

  it('takes several notes at once, one to a line', async () => {
    const user = userEvent.setup()
    const importBulk = vi.mocked(profileKnowledgeAPI.importBulk)
    importBulk.mockResolvedValue([
      {
        id: 9,
        user_id: 1,
        topic: 'How exams look',
        detail: 'Two-hour written papers.',
        created_at: '2026-08-23T09:00:00Z',
        updated_at: '2026-08-23T09:00:00Z',
      },
    ])

    renderAccountPage('/account/background')
    await user.click(await screen.findByRole('button', { name: 'Paste several' }))

    await user.type(
      screen.getByLabelText('Your notes'),
      'How exams look: Two-hour written papers.{enter}Grading: Partial credit for method.',
    )
    await user.click(screen.getByRole('button', { name: /Save 2 notes/ }))

    await waitFor(() =>
      expect(importBulk).toHaveBeenCalledWith({
        items: [
          { topic: 'How exams look', detail: 'Two-hour written papers.' },
          { topic: 'Grading', detail: 'Partial credit for method.' },
        ],
      }),
    )
  })

  it('will not save a paste that has no topic and detail on any line', async () => {
    const user = userEvent.setup()

    renderAccountPage('/account/background')
    await user.click(await screen.findByRole('button', { name: 'Paste several' }))
    await user.type(screen.getByLabelText('Your notes'), 'just some prose with no colon')

    expect(screen.getByRole('button', { name: /Save/ })).toBeDisabled()
  })
})

describe('when the account cannot be saved', () => {
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
        capabilities: ['study_guide'],
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
        capabilities: ['quiz'],
        description: 'Compact OpenAI model',
        is_local: false,
        supports_json: true,
      },
    ])
    mockKnowledgeList.mockResolvedValue([])
    mockGetCreditTransactions.mockResolvedValue([])
    creditState.status = null
  })

  it('claims no saved model when the request was refused', async () => {
    const person = userEvent.setup()
    mockUpdatePreferredModel.mockRejectedValue(new APIError(500, { detail: 'No.' }))
    renderAccountPage('/account/ai')

    await person.selectOptions(
      await screen.findByLabelText('Preferred AI Model'),
      'gpt-4o-mini',
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('No.')
    expect(screen.queryByText(/Preferred AI model updated/)).not.toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('claims no saved level when the request was refused', async () => {
    const person = userEvent.setup()
    mockUpdateEducationLevel.mockRejectedValue(new APIError(422, { detail: 'Unknown level.' }))
    renderAccountPage()

    await person.selectOptions(
      await screen.findByLabelText('What are you studying at?'),
      'graduate',
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('Unknown level.')
    expect(screen.queryByText('Education level updated.')).not.toBeInTheDocument()
  })

  it('says the level was saved only once the request came back', async () => {
    const person = userEvent.setup()
    mockUpdateEducationLevel.mockResolvedValue(undefined as never)
    renderAccountPage()

    await person.selectOptions(
      await screen.findByLabelText('What are you studying at?'),
      'graduate',
    )

    expect(await screen.findByText('Education level updated.')).toBeInTheDocument()
    expect(mockUpdateEducationLevel).toHaveBeenCalledWith('graduate')
    expect(mockRefreshUser).toHaveBeenCalled()
  })

  it('reports a model list that could not be loaded rather than an empty one', async () => {
    mockModelsList.mockRejectedValue(new APIError(503, { detail: 'Models are unavailable.' }))
    renderAccountPage('/account/ai')

    expect(await screen.findByRole('alert')).toHaveTextContent('Models are unavailable.')
  })

  it('keeps a topic on screen when adding it was refused', async () => {
    const person = userEvent.setup()
    mockKnowledgeCreate.mockRejectedValue(new APIError(409, { detail: 'That topic exists.' }))
    renderAccountPage('/account/background')

    await person.click(await screen.findByRole('button', { name: 'Add a note' }))
    await person.type(await screen.findByLabelText('Topic Name'), 'Graph Theory')
    await person.type(
      screen.getByLabelText('Knowledge Details & Background'),
      'Spanning trees.',
    )
    await person.click(screen.getByRole('button', { name: 'Save Topic' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('That topic exists.')
    expect(
      screen.queryByText('Knowledge topic added successfully.'),
    ).not.toBeInTheDocument()
  })

  it('renders profile documents and allows deleting a background document', async () => {
    const person = userEvent.setup()
    mockProfileDocumentsList.mockResolvedValue([
      {
        id: 'doc-123',
        original_file_name: 'quantum_syllabus.pdf',
        file_type: 'pdf',
        mime_type: 'application/pdf',
        file_size: 1024,
        user_id: 1,
        status: 'ready',
        processing_error: null,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    ])
    mockProfileDocumentsDelete.mockResolvedValue(undefined)

    renderAccountPage('/account/background')

    expect(await screen.findByText('quantum_syllabus.pdf')).toBeInTheDocument()
    expect(screen.getByText('Profile Document')).toBeInTheDocument()
    expect(screen.getByText('Ready')).toBeInTheDocument()

    await person.click(screen.getByRole('button', { name: 'Delete quantum_syllabus.pdf' }))
    expect(screen.getByText('Delete “quantum_syllabus.pdf”?')).toBeInTheDocument()

    await person.click(screen.getByRole('button', { name: 'Delete' }))
    expect(mockProfileDocumentsDelete).toHaveBeenCalledWith('doc-123')
  })

  it('renders advertising preference when hosted ads are enabled and allows updating it', async () => {
    const person = userEvent.setup()
    mockAdsGetConfig.mockResolvedValue({
      enabled: true,
      provider: 'ethicalads',
      publisher_id: 'lumina',
    })

    renderAccountPage('/account/appearance')

    expect(await screen.findByText('Privacy & Advertising')).toBeInTheDocument()
    const select = screen.getByLabelText('Advertising preference')
    expect(select).toBeInTheDocument()

    await person.selectOptions(select, 'allowed')
    expect(localStorage.getItem('lumina_ad_consent')).toBe('granted')
  })
})
