import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { systemSettingsAPI, type SystemSettingsInventory } from '@/api/systemSettings';
import { ToastProvider } from '@/ui/ToastProvider';
import SystemSettingsPage from './SystemSettingsPage';

vi.mock('@/api/systemSettings', async () => {
  const actual = await vi.importActual<typeof import('@/api/systemSettings')>(
    '@/api/systemSettings',
  );
  return {
    ...actual,
    systemSettingsAPI: {
      get: vi.fn(),
      update: vi.fn(),
      resetKey: vi.fn(),
      resetAll: vi.fn(),
      restart: vi.fn(),
      restartStatus: vi.fn(),
    },
  };
});

const mockGet = vi.mocked(systemSettingsAPI.get);
const mockUpdate = vi.mocked(systemSettingsAPI.update);
const mockResetKey = vi.mocked(systemSettingsAPI.resetKey);
const mockResetAll = vi.mocked(systemSettingsAPI.resetAll);
const mockRestart = vi.mocked(systemSettingsAPI.restart);

function row(overrides: Partial<SystemSettingsInventory['settings'][number]> = {}) {
  return {
    key: 'RETRIEVAL_CHUNK_LIMIT',
    section: 'Semantic retrieval',
    label: 'Retrieval chunk limit',
    help: 'How many chunks semantic retrieval ranks for one request.',
    kind: 'integer' as const,
    scope: 'overridable' as const,
    risk: 'low' as const,
    secret: false,
    choices: [],
    minimum: 1,
    maximum: 200,
    requires_confirmation: false,
    editable: true,
    source: 'default' as const,
    has_override: false,
    configured: true,
    value: '24',
    default: '24',
    ...overrides,
  };
}

function inventory(
  overrides: Partial<SystemSettingsInventory> = {},
): SystemSettingsInventory {
  return {
    sections: ['Semantic retrieval', 'AI providers'],
    settings: [
      row(),
      row({
        key: 'GEMINI_API_KEY',
        section: 'AI providers',
        label: 'Gemini API key',
        help: 'Credential for the Gemini provider.',
        kind: 'text',
        risk: 'high',
        secret: true,
        minimum: null,
        maximum: null,
        source: 'environment',
        value: null,
        default: null,
      }),
      row({
        key: 'LUMINA_PORT',
        section: 'Semantic retrieval',
        label: 'Published port',
        help: 'The published port for the container.',
        scope: 'compose_managed',
        risk: 'high',
        editable: false,
        minimum: null,
        maximum: null,
        source: 'environment',
        value: '10312',
        default: '10312',
      }),
    ],
    active_revision: 0,
    saved_revision: 0,
    pending_restart: false,
    pending_keys: [],
    override_count: 0,
    saved_at: null,
    supervised_restart: true,
    restart: null,
    rolled_back_from: null,
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <SystemSettingsPage />
      </ToastProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mockGet.mockResolvedValue(inventory());
});

describe('SystemSettingsPage', () => {
  it('lists every supported key with its source', async () => {
    renderPage();

    expect(await screen.findByText('Retrieval chunk limit')).toBeInTheDocument();
    expect(screen.getByText('RETRIEVAL_CHUNK_LIMIT')).toBeInTheDocument();
    expect(screen.getByText('LUMINA_PORT')).toBeInTheDocument();
    expect(screen.getByText('Showing 3 of 3 settings')).toBeInTheDocument();
  });

  it('reports a load failure with a way to retry', async () => {
    mockGet.mockRejectedValue(new APIError(503, { detail: 'down' }));
    renderPage();

    expect(await screen.findByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('never prefills a secret and says whether one is configured', async () => {
    renderPage();
    await screen.findByText('Gemini API key');

    const field = screen.getByLabelText('Gemini API key value');
    expect(field).toHaveValue('');
    expect(screen.getByText(/Leave blank to keep the current value/)).toBeInTheDocument();
  });

  it('renders a compose-level key read-only and says why', async () => {
    renderPage();
    await screen.findByText('Published port');

    expect(screen.queryByLabelText('Published port value')).not.toBeInTheDocument();
    expect(
      screen.getByText('Requires editing .env and re-running docker compose up -d.'),
    ).toBeInTheDocument();
  });

  it('filters by search term', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Retrieval chunk limit');

    await user.type(screen.getByPlaceholderText(/Search by name/), 'gemini');

    await waitFor(() =>
      expect(screen.getByText('Showing 1 of 3 settings')).toBeInTheDocument(),
    );
    expect(screen.queryByText('Retrieval chunk limit')).not.toBeInTheDocument();
  });

  it('reports when nothing matches', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Retrieval chunk limit');

    await user.type(screen.getByPlaceholderText(/Search by name/), 'zzzz');

    expect(await screen.findByText('No settings match')).toBeInTheDocument();
  });

  it('saves edited values as one atomic change', async () => {
    const user = userEvent.setup();
    mockUpdate.mockResolvedValue(
      inventory({ saved_revision: 1, pending_restart: true, pending_keys: ['RETRIEVAL_CHUNK_LIMIT'] }),
    );
    renderPage();
    await screen.findByText('Retrieval chunk limit');

    await user.clear(screen.getByLabelText('Retrieval chunk limit value'));
    await user.type(screen.getByLabelText('Retrieval chunk limit value'), '48');
    await user.click(screen.getByRole('button', { name: 'Save 1 change' }));

    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith({
        expected_revision: 0,
        values: { RETRIEVAL_CHUNK_LIMIT: '48' },
      }),
    );
    expect(await screen.findByText('Settings saved')).toBeInTheDocument();
  });

  it('shows a field error against the setting that caused it', async () => {
    const user = userEvent.setup();
    mockUpdate.mockRejectedValue(
      new APIError(
        422,
        {
          detail: [
            {
              key: 'RETRIEVAL_CHUNK_LIMIT',
              message: 'RETRIEVAL_CHUNK_LIMIT must be between 1 and 200.',
            },
          ],
        },
        'settings_validation_failed',
      ),
    );
    renderPage();
    await screen.findByText('Retrieval chunk limit');

    await user.clear(screen.getByLabelText('Retrieval chunk limit value'));
    await user.type(screen.getByLabelText('Retrieval chunk limit value'), '9999');
    await user.click(screen.getByRole('button', { name: 'Save 1 change' }));

    expect(
      await screen.findByText('RETRIEVAL_CHUNK_LIMIT must be between 1 and 200.'),
    ).toBeInTheDocument();
  });

  it('reloads after a stale revision conflict', async () => {
    const user = userEvent.setup();
    mockUpdate.mockRejectedValue(
      new APIError(
        409,
        { success: false, message: 'Another administrator changed configuration.' },
        'settings_revision_conflict',
      ),
    );
    renderPage();
    await screen.findByText('Retrieval chunk limit');

    await user.clear(screen.getByLabelText('Retrieval chunk limit value'));
    await user.type(screen.getByLabelText('Retrieval chunk limit value'), '48');
    await user.click(screen.getByRole('button', { name: 'Save 1 change' }));

    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
  });

  it('discards edits without calling the API', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Retrieval chunk limit');

    await user.clear(screen.getByLabelText('Retrieval chunk limit value'));
    await user.type(screen.getByLabelText('Retrieval chunk limit value'), '48');
    await user.click(screen.getByRole('button', { name: 'Discard edits' }));

    expect(
      screen.queryByRole('button', { name: /^Save \d+ change/ }),
    ).not.toBeInTheDocument();
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  it('resets one override under its own accessible name', async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue(
      inventory({
        settings: [row({ has_override: true, source: 'override', value: '48' })],
        override_count: 1,
        saved_revision: 2,
      }),
    );
    mockResetKey.mockResolvedValue(inventory({ saved_revision: 3 }));
    renderPage();

    await user.click(
      await screen.findByRole('button', { name: 'Reset RETRIEVAL_CHUNK_LIMIT' }),
    );

    await waitFor(() =>
      expect(mockResetKey).toHaveBeenCalledWith('RETRIEVAL_CHUNK_LIMIT', 2),
    );
  });

  it('resets every override behind a confirmation', async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue(inventory({ override_count: 2, saved_revision: 4 }));
    mockResetAll.mockResolvedValue(inventory({ saved_revision: 5 }));
    renderPage();

    await user.click(
      await screen.findByRole('button', { name: 'Reset all overrides' }),
    );
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Reset overrides' }));

    await waitFor(() => expect(mockResetAll).toHaveBeenCalledWith(4));
  });

  it('requires a typed confirmation for a high-risk setting', async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue(
      inventory({
        settings: [
          row({
            key: 'JWT_SECRET_KEY',
            label: 'JWT secret key',
            section: 'Semantic retrieval',
            kind: 'text',
            risk: 'high',
            secret: true,
            requires_confirmation: true,
            minimum: null,
            maximum: null,
            value: null,
            default: null,
          }),
        ],
      }),
    );
    mockUpdate.mockResolvedValue(inventory({ saved_revision: 1 }));
    renderPage();

    await user.type(
      await screen.findByLabelText('JWT secret key value'),
      'a-brand-new-secret-value-that-is-long',
    );
    await user.click(screen.getByRole('button', { name: 'Save 1 change' }));

    const dialog = await screen.findByRole('dialog');
    expect(mockUpdate).not.toHaveBeenCalled();
    await user.type(within(dialog).getByLabelText(/Type SAVE/), 'SAVE');
    await user.click(within(dialog).getByRole('button', { name: 'Save changes' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
  });
});

describe('RestartSection', () => {
  it('says there is nothing to apply when the saved revision is active', async () => {
    renderPage();

    expect(
      await screen.findByText(
        'The saved configuration is already active, so there is nothing to apply.',
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Restart Lumina' }),
    ).not.toBeInTheDocument();
  });

  it('omits the restart button when nothing supervises the process', async () => {
    mockGet.mockResolvedValue(
      inventory({
        supervised_restart: false,
        pending_restart: true,
        saved_revision: 1,
        pending_keys: ['RETRIEVAL_CHUNK_LIMIT'],
      }),
    );
    renderPage();

    expect(
      await screen.findByText(/Nothing is configured to restart Lumina automatically/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Restart Lumina' }),
    ).not.toBeInTheDocument();
  });

  it('requests a restart after a typed confirmation', async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue(
      inventory({
        pending_restart: true,
        saved_revision: 1,
        pending_keys: ['RETRIEVAL_CHUNK_LIMIT'],
      }),
    );
    mockRestart.mockResolvedValue({
      request_id: 'req-1',
      target_revision: 1,
      state: 'queued',
      in_flight: { total: 0 },
    });
    renderPage();

    await user.click(await screen.findByRole('button', { name: 'Restart Lumina' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('RETRIEVAL_CHUNK_LIMIT')).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText(/Type RESTART/), 'RESTART');
    await user.click(within(dialog).getByRole('button', { name: 'Restart now' }));

    await waitFor(() => expect(mockRestart).toHaveBeenCalledWith(1));
    expect(await screen.findByText('Restart requested')).toBeInTheDocument();
  });

  it('warns that a previous restart was rolled back', async () => {
    mockGet.mockResolvedValue(inventory({ rolled_back_from: 4 }));
    renderPage();

    expect(
      await screen.findByText('A previous restart was rolled back'),
    ).toBeInTheDocument();
  });

  it('refuses to restart while edits are unsaved', async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue(
      inventory({
        pending_restart: true,
        saved_revision: 1,
        pending_keys: ['RETRIEVAL_CHUNK_LIMIT'],
      }),
    );
    renderPage();
    await screen.findByText('Retrieval chunk limit');

    await user.clear(screen.getByLabelText('Retrieval chunk limit value'));
    await user.type(screen.getByLabelText('Retrieval chunk limit value'), '48');

    expect(
      screen.getByText('Save or discard your edits before restarting.'),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Restart Lumina' }),
    ).not.toBeInTheDocument();
  });
});
