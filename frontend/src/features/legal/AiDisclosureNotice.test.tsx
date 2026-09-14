import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { legalAPI } from '@/api/legal';
import { AiDisclosureNotice } from './AiDisclosureNotice';

vi.mock('@/api/legal', () => ({
  legalAPI: {
    getConfig: vi.fn(),
  },
}));

const mockedLegalConfig = vi.mocked(legalAPI.getConfig);

function renderNotice() {
  return render(
    <MemoryRouter>
      <AiDisclosureNotice />
    </MemoryRouter>,
  );
}

describe('AiDisclosureNotice', () => {
  it('links to the AI disclosure in one short line when the legal package is enabled', async () => {
    mockedLegalConfig.mockResolvedValue({
      enabled: true,
      terms_version: '1.0',
      privacy_version: '1.0',
      effective_date: '2026-09-12',
    });

    renderNotice();

    expect(await screen.findByText(/AI-generated content can be wrong/)).toBeVisible();
    expect(screen.getByRole('link', { name: 'How Lumina uses AI' })).toHaveAttribute(
      'href',
      '/legal/ai-disclosure',
    );
  });

  it('renders nothing when the legal package is disabled', async () => {
    mockedLegalConfig.mockResolvedValue({
      enabled: false,
      terms_version: null,
      privacy_version: null,
      effective_date: null,
    });

    const { container } = renderNotice();

    await waitFor(() => expect(mockedLegalConfig).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
