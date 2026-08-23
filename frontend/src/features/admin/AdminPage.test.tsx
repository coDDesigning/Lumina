import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { adminAPI } from '@/api/admin';
import type { CreditTransaction, User } from '@/api/types';
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
    banUser: vi.fn(),
    changeUserRole: vi.fn(),
    changeCredits: vi.fn(),
    listUserCreditTransactions: vi.fn(),
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
    mocked.listUserCreditTransactions.mockResolvedValue([]);
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
