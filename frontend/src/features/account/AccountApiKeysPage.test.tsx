import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { userAPI } from '@/api/user';
import { ToastProvider } from '@/ui/ToastProvider';
import AccountApiKeysPage from './AccountApiKeysPage';

const authState = {
  user: {
    id: 1,
    name: 'Admin User',
    email: 'admin@example.com',
    role: 'admin',
    is_banned: false,
    is_email_verified: true,
    credits: null,
    preferred_model: 'gemini-1.5-flash',
    education_level: 'unspecified',
  },
};

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({
    user: authState.user,
    isAuthenticated: true,
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
  }),
}));

vi.mock('@/api/user', () => ({
  userAPI: {
    getApiKeys: vi.fn(),
    updateApiKeys: vi.fn(),
  },
}));

const mockGetApiKeys = vi.mocked(userAPI.getApiKeys);
const mockUpdateApiKeys = vi.mocked(userAPI.updateApiKeys);

function renderPage() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <AccountApiKeysPage />
      </ToastProvider>
    </MemoryRouter>,
  );
}

describe('AccountApiKeysPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.user.role = 'admin';
  });

  it('renders provider sections and input fields with system default badges when unconfigured', async () => {
    mockGetApiKeys.mockResolvedValue({
      openai_api_key: null,
      gemini_api_key: null,
      anthropic_api_key: null,
      has_openai_key: false,
      has_gemini_key: false,
      has_anthropic_key: false,
    });

    renderPage();

    expect(await screen.findByRole('heading', { name: 'API keys' })).toBeInTheDocument();
    expect(screen.getByText('OpenAI')).toBeInTheDocument();
    expect(screen.getByText('Google Gemini')).toBeInTheDocument();
    expect(screen.getByText('Anthropic Claude')).toBeInTheDocument();

    const badges = screen.getAllByText('System default');
    expect(badges).toHaveLength(3);

    expect(screen.getByLabelText('OpenAI API Key')).toHaveAttribute('type', 'password');
    expect(screen.getByLabelText('Gemini API Key')).toHaveAttribute('type', 'password');
    expect(screen.getByLabelText('Anthropic API Key')).toHaveAttribute('type', 'password');
  });

  it('displays configured badges and masked placeholders when keys are set', async () => {
    mockGetApiKeys.mockResolvedValue({
      openai_api_key: 'sk-pro...****',
      gemini_api_key: 'AIzaSy...****',
      anthropic_api_key: null,
      has_openai_key: true,
      has_gemini_key: true,
      has_anthropic_key: false,
    });

    renderPage();

    expect(await screen.findByPlaceholderText('sk-pro...****')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('AIzaSy...****')).toBeInTheDocument();

    const configuredBadges = screen.getAllByText('Configured');
    expect(configuredBadges).toHaveLength(2);

    expect(screen.getByText('System default')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Clear all keys' })).toBeInTheDocument();
  });

  it('toggles password visibility with the eye icon button', async () => {
    const user = userEvent.setup();
    mockGetApiKeys.mockResolvedValue({
      openai_api_key: null,
      gemini_api_key: null,
      anthropic_api_key: null,
      has_openai_key: false,
      has_gemini_key: false,
      has_anthropic_key: false,
    });

    renderPage();

    const openaiInput = await screen.findByLabelText('OpenAI API Key');
    expect(openaiInput).toHaveAttribute('type', 'password');

    const showButton = screen.getByRole('button', { name: 'Show OpenAI API key' });
    await user.click(showButton);

    expect(openaiInput).toHaveAttribute('type', 'text');
    expect(screen.getByRole('button', { name: 'Hide OpenAI API key' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Hide OpenAI API key' }));
    expect(openaiInput).toHaveAttribute('type', 'password');
  });

  it('submits updated keys and displays success notifications', async () => {
    const user = userEvent.setup();
    mockGetApiKeys.mockResolvedValue({
      openai_api_key: null,
      gemini_api_key: null,
      anthropic_api_key: null,
      has_openai_key: false,
      has_gemini_key: false,
      has_anthropic_key: false,
    });
    mockUpdateApiKeys.mockResolvedValue({
      openai_api_key: 'sk-pro...****',
      gemini_api_key: 'AIzaSy...****',
      anthropic_api_key: null,
      has_openai_key: true,
      has_gemini_key: true,
      has_anthropic_key: false,
    });

    renderPage();

    const openaiInput = await screen.findByLabelText('OpenAI API Key');
    const geminiInput = screen.getByLabelText('Gemini API Key');

    await user.type(openaiInput, 'sk-proj-test12345678');
    await user.type(geminiInput, 'AIzaSyTestApiKey123');

    const saveButton = screen.getByRole('button', { name: 'Save API keys' });
    await user.click(saveButton);

    await waitFor(() => {
      expect(mockUpdateApiKeys).toHaveBeenCalledWith({
        openai_api_key: 'sk-proj-test12345678',
        gemini_api_key: 'AIzaSyTestApiKey123',
      });
    });

    expect(await screen.findByText('Your API keys have been saved securely.')).toBeInTheDocument();
  });

  it('allows clearing a single configured key', async () => {
    const user = userEvent.setup();
    mockGetApiKeys.mockResolvedValue({
      openai_api_key: 'sk-pro...****',
      gemini_api_key: null,
      anthropic_api_key: null,
      has_openai_key: true,
      has_gemini_key: false,
      has_anthropic_key: false,
    });
    mockUpdateApiKeys.mockResolvedValue({
      openai_api_key: null,
      gemini_api_key: null,
      anthropic_api_key: null,
      has_openai_key: false,
      has_gemini_key: false,
      has_anthropic_key: false,
    });

    renderPage();

    const clearButton = await screen.findByRole('button', { name: 'Clear key' });
    await user.click(clearButton);

    await waitFor(() => {
      expect(mockUpdateApiKeys).toHaveBeenCalledWith({
        openai_api_key: '',
      });
    });

    expect(await screen.findByText('OpenAI API key removed.')).toBeInTheDocument();
  });

  it('allows clearing all configured keys at once', async () => {
    const user = userEvent.setup();
    mockGetApiKeys.mockResolvedValue({
      openai_api_key: 'sk-pro...****',
      gemini_api_key: 'AIzaSy...****',
      anthropic_api_key: null,
      has_openai_key: true,
      has_gemini_key: true,
      has_anthropic_key: false,
    });
    mockUpdateApiKeys.mockResolvedValue({
      openai_api_key: null,
      gemini_api_key: null,
      anthropic_api_key: null,
      has_openai_key: false,
      has_gemini_key: false,
      has_anthropic_key: false,
    });

    renderPage();

    const clearAllButton = await screen.findByRole('button', { name: 'Clear all keys' });
    await user.click(clearAllButton);

    await waitFor(() => {
      expect(mockUpdateApiKeys).toHaveBeenCalledWith({
        openai_api_key: '',
        gemini_api_key: '',
        anthropic_api_key: '',
      });
    });

    expect(await screen.findByText('All custom API keys have been cleared.')).toBeInTheDocument();
  });

  it('displays an error alert when saving fails', async () => {
    const user = userEvent.setup();
    mockGetApiKeys.mockResolvedValue({
      openai_api_key: null,
      gemini_api_key: null,
      anthropic_api_key: null,
      has_openai_key: false,
      has_gemini_key: false,
      has_anthropic_key: false,
    });
    mockUpdateApiKeys.mockRejectedValue(
      new APIError(500, { detail: 'Server failed to encrypt keys.' }),
    );

    renderPage();

    const openaiInput = await screen.findByLabelText('OpenAI API Key');
    await user.type(openaiInput, 'sk-test');

    const saveButton = screen.getByRole('button', { name: 'Save API keys' });
    await user.click(saveButton);

    expect(await screen.findByText('Server failed to encrypt keys.')).toBeInTheDocument();
  });

  it('validates OpenAI API key prefix and prevents submission when invalid', async () => {
    const user = userEvent.setup();
    mockGetApiKeys.mockResolvedValue({
      openai_api_key: null,
      gemini_api_key: null,
      anthropic_api_key: null,
      has_openai_key: false,
      has_gemini_key: false,
      has_anthropic_key: false,
    });

    renderPage();

    const openaiInput = await screen.findByLabelText('OpenAI API Key');
    await user.type(openaiInput, 'invalid-prefix-key-1234');

    const saveButton = screen.getByRole('button', { name: 'Save API keys' });
    await user.click(saveButton);

    expect(mockUpdateApiKeys).not.toHaveBeenCalled();
    expect(await screen.findByText("OpenAI API key must start with 'sk-'.")).toBeInTheDocument();
  });

  it('validates Anthropic API key prefix and prevents submission when invalid', async () => {
    const user = userEvent.setup();
    mockGetApiKeys.mockResolvedValue({
      openai_api_key: null,
      gemini_api_key: null,
      anthropic_api_key: null,
      has_openai_key: false,
      has_gemini_key: false,
      has_anthropic_key: false,
    });

    renderPage();

    const anthropicInput = await screen.findByLabelText('Anthropic API Key');
    await user.type(anthropicInput, 'sk-notant-invalid-key');

    const saveButton = screen.getByRole('button', { name: 'Save API keys' });
    await user.click(saveButton);

    expect(mockUpdateApiKeys).not.toHaveBeenCalled();
    expect(await screen.findByText("Anthropic API key must start with 'sk-ant-'.")).toBeInTheDocument();
  });

  it('redirects non-admin users away from the API keys page', () => {
    authState.user.role = 'Student';
    renderPage();
    expect(screen.queryByRole('heading', { name: 'API keys' })).not.toBeInTheDocument();
  });
});
