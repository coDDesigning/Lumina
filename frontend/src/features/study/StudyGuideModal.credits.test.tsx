import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { studyGuideAPI } from '@/api/studyGuide';
import { userAPI } from '@/api/user';
import type { CreditStatus } from '@/api/types';
import { CreditProvider } from '@/context/CreditContext';
import { StudyGuideModal } from './StudyGuideModal';

vi.mock('@/api/studyGuide', () => ({ studyGuideAPI: { enqueue: vi.fn() } }));
vi.mock('@/api/user', () => ({ userAPI: { getCredits: vi.fn() } }));
vi.mock('@/api/settings', () => ({ settingsAPI: { get: vi.fn().mockResolvedValue({}) } }));
vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: true, user: { id: 1 } }),
}));

const mockEnqueue = vi.mocked(studyGuideAPI.enqueue);
const mockGetCredits = vi.mocked(userAPI.getCredits);

function status(credits: number | null): CreditStatus {
  return {
    credits,
    metering_enabled: credits !== null,
    email_verification_required: false,
    is_email_verified: true,
    monthly_grant: credits === null ? null : 50,
    balance_cap: credits === null ? null : 100,
    next_grant_at: credits === null ? null : '2026-09-01T00:00:00Z',
    generation_costs: credits === null ? {} : { study_guide: 1 },
  };
}

function renderModal() {
  render(
    <CreditProvider>
      <StudyGuideModal
        courseId={1}
        courseName="Algorithms"
        topics={[]}
        readyDocumentCount={1}
        onClose={vi.fn()}
      />
    </CreditProvider>,
  );
}

beforeEach(() => {
  mockEnqueue.mockResolvedValue({ job_id: 2, status: 'queued' });
});

describe('StudyGuideModal credit handling', () => {
  it('blocks enqueue and explains a balance that cannot cover it', async () => {
    mockGetCredits.mockResolvedValue(status(0));
    renderModal();
    await waitFor(() => expect(screen.getByRole('button', { name: /write my study guide/i })).toBeDisabled());
    expect(screen.getByRole('alert').textContent).toMatch(/don't have enough credits/i);
    expect(mockEnqueue).not.toHaveBeenCalled();
  });

  it('refreshes the server balance after accepting the prepaid job', async () => {
    mockGetCredits.mockResolvedValueOnce(status(12)).mockResolvedValue(status(11));
    renderModal();
    await waitFor(() => expect(screen.getByText('12')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /write my study guide/i }));
    await waitFor(() => expect(mockGetCredits).toHaveBeenCalledTimes(2));
  });

  it('shows no credit interface for an unmetered account', async () => {
    mockGetCredits.mockResolvedValue(status(null));
    renderModal();
    await screen.findByRole('button', { name: /write my study guide/i });
    expect(screen.queryByText(/credit/i)).toBeNull();
  });
});
