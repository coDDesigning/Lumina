import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { adminAPI } from '@/api/admin';
import type { AiCostReport, CreditTransaction, User } from '@/api/types';
import { ToastProvider } from '@/ui/ToastProvider'
import AdminPage from './AdminPage';

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

vi.mock('@/api/admin', () => ({
  adminAPI: {
    listUsers: vi.fn(),
    getAiCostReport: vi.fn(),
    banUser: vi.fn(),
    changeUserRole: vi.fn(),
    changeCredits: vi.fn(),
    listUserCreditTransactions: vi.fn(),
    listUserCourses: vi.fn(),
  },
}));

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 1,
      name: 'Root',
      email: 'admin@example.com',
      role: 'admin',
      is_banned: false,
      credits: null,
      preferred_model: 'gemini:gemini-3.6-flash',
      education_level: 'unspecified',
    },
    isAuthenticated: true,
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

const ADMIN: User = {
  id: 1,
  name: 'Root',
  email: 'admin@example.com',
  role: 'admin',
  is_banned: false,
  credits: null,
  preferred_model: 'gemini:gemini-3.6-flash',
  education_level: 'unspecified',
};

const LEARNER: User = {
  id: 2,
  name: 'Alice',
  email: 'alice@example.com',
  role: 'user',
  is_banned: false,
  credits: 0,
  preferred_model: 'gemini:gemini-3.6-flash',
  education_level: 'unspecified',
};

const SECOND_LEARNER: User = {
  ...LEARNER,
  id: 3,
  name: 'Bob',
  email: 'bob@example.com',
};

const TRANSACTION: CreditTransaction = {
  id: 9,
  delta: 10,
  balance_after: 10,
  reason: 'support_compensation',
  actor_type: 'admin',
  actor_user_id: 1,
  actor_label: 'admin@example.com',
  source_type: null,
  source_id: null,
  refunds_transaction_id: null,
  grant_period: null,
  note: 'Outage INC-123',
  created_at: '2026-08-21T10:00:00Z',
};

const COST_REPORT: AiCostReport = {
  timezone: 'UTC',
  start_date: '2026-07-26',
  end_date: '2026-08-24',
  totals: {
    successful_generations: 2,
    prompt_tokens: 100_050,
    completion_tokens: 200_100,
    estimated_cost_usd: 0.5,
    unpriced_generations: 1,
  },
  daily: [
    {
      date: '2026-08-24',
      provider: 'gemini',
      model: 'gemini-2.5-flash',
      pricing_version: '2026-08-24',
      successful_generations: 1,
      prompt_tokens: 100_000,
      completion_tokens: 200_000,
      estimated_cost_usd: 0.5,
      unpriced_generations: 0,
    },
  ],
};

const mocked = vi.mocked(adminAPI);

function renderPage() {
  return render(
    <ToastProvider>
      <MemoryRouter>
        <AdminPage />
      </MemoryRouter>
    </ToastProvider>,
  );
}

async function openDialog() {
  renderPage();
  const row = await screen.findByText('alice@example.com');
  const actions = row.closest('tr');
  await userEvent.click(within(actions as HTMLElement).getByText('Credits'));
  return screen.getByRole('dialog');
}

describe('AdminPage credit administration', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.listUsers.mockResolvedValue([ADMIN, LEARNER]);
    mocked.getAiCostReport.mockResolvedValue(COST_REPORT);
    mocked.listUserCreditTransactions.mockResolvedValue([]);
  });

  it('shows persisted provider cost estimates and their pricing version', async () => {
    renderPage();

    expect(await screen.findAllByText('$0.5000')).toHaveLength(2);
    expect(screen.getByText('gemini / gemini-2.5-flash')).toBeInTheDocument();
    expect(screen.getAllByText('2026-08-24')).toHaveLength(2);
    expect(screen.getByText('Unpriced generations')).toBeInTheDocument();
  });

  it('opens a dialog naming the account and its current balance', async () => {
    const dialog = await openDialog();

    expect(within(dialog).getByText('alice@example.com')).toBeTruthy();
    expect(within(dialog).getByText('Current balance: 0')).toBeTruthy();
  });

  it('applies a signed change and shows the balance it produced', async () => {
    mocked.changeCredits.mockResolvedValue({
      user: { ...LEARNER, credits: 10 },
      transaction: TRANSACTION,
    });

    const dialog = await openDialog();
    await userEvent.type(within(dialog).getByLabelText(/Credit change/), '10');
    await userEvent.selectOptions(
      within(dialog).getByLabelText('Reason'),
      'support_compensation',
    );
    await userEvent.type(
      within(dialog).getByLabelText('Note (optional)'),
      'Outage INC-123',
    );
    await userEvent.click(within(dialog).getByText('Apply'));

    await waitFor(() =>
      expect(mocked.changeCredits).toHaveBeenCalledWith(
        'alice@example.com',
        10,
        'support_compensation',
        'Outage INC-123',
      ),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    const row = screen.getByText('alice@example.com').closest('tr');
    expect(within(row as HTMLElement).getByText('10')).toBeTruthy();
  });

  it('updates an open ledger after applying a credit change', async () => {
    const prior = {
      ...TRANSACTION,
      id: 8,
      delta: -1,
      balance_after: 0,
      reason: 'generation_charge' as const,
      source_type: 'study_guide',
    };
    mocked.listUserCreditTransactions.mockResolvedValue([prior]);
    mocked.changeCredits.mockResolvedValue({
      user: { ...LEARNER, credits: 10 },
      transaction: TRANSACTION,
    });
    renderPage();
    const email = await screen.findByText('alice@example.com');
    const row = email.closest('tr') as HTMLElement;

    await userEvent.click(within(row).getByText('Ledger'));
    expect(await screen.findByText(/Study guide/)).toBeInTheDocument();
    await userEvent.click(within(row).getByText('Credits'));
    const dialog = screen.getByRole('dialog');
    await userEvent.type(within(dialog).getByLabelText(/Credit change/), '10');
    await userEvent.selectOptions(
      within(dialog).getByLabelText('Reason'),
      'support_compensation',
    );
    await userEvent.click(within(dialog).getByText('Apply'));

    expect(await screen.findByText('Support compensation')).toBeInTheDocument();
    expect(mocked.listUserCreditTransactions).toHaveBeenCalledTimes(1);
  });

  it('ignores a ledger response after another account is opened', async () => {
    let resolveAlice: (transactions: CreditTransaction[]) => void = () => {};
    const aliceTransaction = {
      ...TRANSACTION,
      id: 7,
      reason: 'initial_grant' as const,
    };
    const bobTransaction = {
      ...TRANSACTION,
      id: 8,
      reason: 'periodic_grant' as const,
    };
    mocked.listUsers.mockResolvedValue([ADMIN, LEARNER, SECOND_LEARNER]);
    mocked.listUserCreditTransactions.mockImplementation((email) => {
      if (email === LEARNER.email) {
        return new Promise((resolve) => {
          resolveAlice = resolve;
        });
      }
      return Promise.resolve([bobTransaction]);
    });
    renderPage();
    const aliceRow = (await screen.findByText(LEARNER.email)).closest('tr') as HTMLElement;
    const bobRow = screen.getByText(SECOND_LEARNER.email).closest('tr') as HTMLElement;

    await userEvent.click(within(aliceRow).getByText('Ledger'));
    await userEvent.click(within(bobRow).getByText('Ledger'));
    expect(await screen.findByText('Monthly credits')).toBeInTheDocument();

    await act(async () => resolveAlice([aliceTransaction]));

    expect(screen.queryByText('Initial credits')).toBeNull();
    expect(screen.getByText('Monthly credits')).toBeInTheDocument();
  });

  it("does not append one account's adjustment to another account's ledger", async () => {
    let resolveAdjustment: (
      result: Awaited<ReturnType<typeof adminAPI.changeCredits>>,
    ) => void = () => {};
    const bobTransaction = {
      ...TRANSACTION,
      id: 8,
      reason: 'periodic_grant' as const,
    };
    mocked.listUsers.mockResolvedValue([ADMIN, LEARNER, SECOND_LEARNER]);
    mocked.listUserCreditTransactions.mockImplementation((email) =>
      Promise.resolve(email === SECOND_LEARNER.email ? [bobTransaction] : []),
    );
    mocked.changeCredits.mockReturnValue(
      new Promise((resolve) => {
        resolveAdjustment = resolve;
      }),
    );
    renderPage();
    const aliceRow = (await screen.findByText(LEARNER.email)).closest('tr') as HTMLElement;
    const bobRow = screen.getByText(SECOND_LEARNER.email).closest('tr') as HTMLElement;

    await userEvent.click(within(aliceRow).getByText('Ledger'));
    await waitFor(() => expect(mocked.listUserCreditTransactions).toHaveBeenCalledTimes(1));
    await userEvent.click(within(aliceRow).getByText('Credits'));
    const dialog = screen.getByRole('dialog');
    await userEvent.type(within(dialog).getByLabelText(/Credit change/), '10');
    await userEvent.selectOptions(
      within(dialog).getByLabelText('Reason'),
      'support_compensation',
    );
    await userEvent.click(within(dialog).getByText('Apply'));
    await waitFor(() => expect(mocked.changeCredits).toHaveBeenCalled());
    await userEvent.click(within(dialog).getByText('Cancel'));
    await userEvent.click(within(bobRow).getByText('Ledger'));
    expect(await screen.findByText('Monthly credits')).toBeInTheDocument();

    await act(async () =>
      resolveAdjustment({
        user: { ...LEARNER, credits: 10 },
        transaction: TRANSACTION,
      }),
    );

    expect(screen.queryByText('Support compensation')).toBeNull();
    expect(screen.getByText('Monthly credits')).toBeInTheDocument();
  });

  it('removes credits when the delta is negative', async () => {
    mocked.changeCredits.mockResolvedValue({
      user: { ...LEARNER, credits: 0 },
      transaction: { ...TRANSACTION, delta: -5, reason: 'admin_adjustment' },
    });

    const dialog = await openDialog();
    await userEvent.type(within(dialog).getByLabelText(/Credit change/), '-5');
    await userEvent.selectOptions(
      within(dialog).getByLabelText('Reason'),
      'admin_adjustment',
    );
    await userEvent.click(within(dialog).getByText('Apply'));

    await waitFor(() =>
      expect(mocked.changeCredits).toHaveBeenCalledWith(
        'alice@example.com',
        -5,
        'admin_adjustment',
        undefined,
      ),
    );
  });

  it('refuses a negative delta for a grant without calling the API', async () => {
    const dialog = await openDialog();
    await userEvent.type(within(dialog).getByLabelText(/Credit change/), '-5');
    await userEvent.click(within(dialog).getByText('Apply'));

    expect(within(dialog).getByRole('alert').textContent).toContain(
      'can only add credits',
    );
    expect(mocked.changeCredits).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('refuses a zero change without calling the API', async () => {
    const dialog = await openDialog();
    await userEvent.type(within(dialog).getByLabelText(/Credit change/), '0');
    await userEvent.click(within(dialog).getByText('Apply'));

    expect(within(dialog).getByRole('alert')).toBeTruthy();
    expect(mocked.changeCredits).not.toHaveBeenCalled();
  });

  it('keeps the dialog open and explains a rejection from the server', async () => {
    mocked.changeCredits.mockRejectedValue(
      new APIError(400, {
        detail: 'A credit adjustment cannot take the balance below zero',
      }),
    );

    const dialog = await openDialog();
    await userEvent.type(within(dialog).getByLabelText(/Credit change/), '-5');
    await userEvent.selectOptions(
      within(dialog).getByLabelText('Reason'),
      'admin_adjustment',
    );
    await userEvent.click(within(dialog).getByText('Apply'));

    await waitFor(() =>
      expect(within(dialog).getByRole('alert').textContent).toContain(
        'below zero',
      ),
    );
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('closes on cancel without changing anything', async () => {
    const dialog = await openDialog();
    await userEvent.click(within(dialog).getByText('Cancel'));

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(mocked.changeCredits).not.toHaveBeenCalled();
  });
});

describe('AdminPage — user courses support workflow', () => {
  it('toggles courses list for a user and displays links to courses', async () => {
    mocked.listUsers.mockResolvedValue([ADMIN, LEARNER]);
    mocked.listUserCourses.mockResolvedValue([
      {
        id: 42,
        title: 'Distributed Systems',
        subject_area: 'Computer Science',
        education_level: 'undergraduate',
        description: null,
        owner_id: LEARNER.id,
        created_at: '2026-08-01T00:00:00Z',
        updated_at: '2026-08-02T00:00:00Z',
        semester: 'Fall 2026',
        exam_date: null,
        syllabus: null,
        topics: null,
      },
    ]);

    render(
      <ToastProvider>
        <MemoryRouter>
          <AdminPage />
        </MemoryRouter>
      </ToastProvider>,
    );

    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument());

    const aliceRow = screen.getByText('alice@example.com').closest('tr')!;
    const coursesBtn = within(aliceRow).getByRole('button', { name: 'Courses' });

    await userEvent.click(coursesBtn);

    expect(mocked.listUserCourses).toHaveBeenCalledWith('alice@example.com');
    await waitFor(() =>
      expect(screen.getByText('Distributed Systems')).toBeInTheDocument(),
    );
    expect(screen.getByRole('link', { name: 'View Course' })).toHaveAttribute(
      'href',
      '/courses/42',
    );

    // Clicking again closes the drawer
    await userEvent.click(coursesBtn);
    expect(screen.queryByText('Distributed Systems')).not.toBeInTheDocument();
  });

  it('shows empty state when a user has no active courses', async () => {
    mocked.listUsers.mockResolvedValue([ADMIN, LEARNER]);
    mocked.listUserCourses.mockResolvedValue([]);

    render(
      <ToastProvider>
        <MemoryRouter>
          <AdminPage />
        </MemoryRouter>
      </ToastProvider>,
    );

    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument());

    const aliceRow = screen.getByText('alice@example.com').closest('tr')!;
    await userEvent.click(within(aliceRow).getByRole('button', { name: 'Courses' }));

    await waitFor(() =>
      expect(
        screen.getByText('No active courses found for this user.'),
      ).toBeInTheDocument(),
    );
  });
});
