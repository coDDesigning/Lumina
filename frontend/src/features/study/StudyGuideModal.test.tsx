import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { studyGuideAPI } from '@/api/studyGuide';
import { userAPI } from '@/api/user';
import type { CreditStatus } from '@/api/types';
import { CreditProvider } from '@/context/CreditContext';
import { StudyGuideModal } from './StudyGuideModal';

vi.mock('@/api/studyGuide', () => ({
  studyGuideAPI: { enqueue: vi.fn() },
}));

vi.mock('@/api/settings', () => ({
  settingsAPI: {
    get: vi.fn().mockResolvedValue({ summary_length: 'medium', detail_level: 'standard' }),
  },
}));

vi.mock('@/api/user', () => ({ userAPI: { getCredits: vi.fn() } }));
vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: true, user: { id: 1 } }),
}));

const mockEnqueue = vi.mocked(studyGuideAPI.enqueue);
const mockGetCredits = vi.mocked(userAPI.getCredits);
const UNMETERED: CreditStatus = {
  credits: null,
  metering_enabled: false,
  email_verification_required: false,
  is_email_verified: true,
  monthly_grant: null,
  balance_cap: null,
  next_grant_at: null,
  generation_costs: {},
};

function renderModal(readyDocumentCount = 3) {
  const onClose = vi.fn();
  const onQueued = vi.fn();
  render(
    <CreditProvider>
      <StudyGuideModal
        courseId={1}
        courseName="Algorithms"
        topics={['Sorting', 'Graphs']}
        readyDocumentCount={readyDocumentCount}
        onClose={onClose}
        onQueued={onQueued}
      />
    </CreditProvider>,
  );
  return { onClose, onQueued, person: userEvent.setup() };
}

beforeEach(() => {
  mockGetCredits.mockResolvedValue(UNMETERED);
  mockEnqueue.mockResolvedValue({ job_id: 41, status: 'queued' });
});

describe('queueing a study guide', () => {
  it('keeps the setup visible while the short enqueue request is pending', async () => {
    mockEnqueue.mockReturnValue(new Promise(() => {}));
    const { person } = renderModal();

    await person.click(await screen.findByRole('button', { name: /write my study guide/i }));

    expect(screen.getByLabelText(/what kind of guide/i)).toBeInTheDocument();
    expect(screen.queryByText(/reading your material/i)).toBeNull();
  });

  it('sends every selected option and closes as soon as the job is accepted', async () => {
    const { person, onClose, onQueued } = renderModal();
    await person.selectOptions(await screen.findByLabelText(/what kind of guide/i), 'exam_tips');
    await person.selectOptions(screen.getByLabelText(/which topic/i), 'Graphs');
    await person.selectOptions(screen.getByLabelText(/how long/i), 'long');
    await person.selectOptions(screen.getByLabelText(/how deep/i), 'detailed');
    await person.selectOptions(screen.getByLabelText(/what it is for/i), 'exam_focused');
    await person.click(screen.getByRole('checkbox', { name: /study profile/i }));
    await person.click(screen.getByRole('button', { name: /write my study guide/i }));

    await waitFor(() => expect(mockEnqueue).toHaveBeenCalled());
    expect(mockEnqueue.mock.calls[0][1]).toMatchObject({
      summary_format: 'exam_tips',
      topic_focus: 'Graphs',
      summary_length: 'long',
      detail_level: 'detailed',
      summary_mode: 'exam_focused',
      use_profile_knowledge: true,
    });
    await waitFor(() => expect(onQueued).toHaveBeenCalledWith(41));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('does not enqueue without ready material', async () => {
    renderModal(0);
    expect(await screen.findByText('There is nothing to work from yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /write my study guide/i })).toBeDisabled();
    expect(mockEnqueue).not.toHaveBeenCalled();
  });

  it('keeps enqueue failures in the setup dialog with a recovery action', async () => {
    mockEnqueue.mockRejectedValue(
      new APIError(503, { detail: 'The AI service is currently unavailable.' }, 'provider_unavailable'),
    );
    const { person, onClose } = renderModal();
    await person.click(await screen.findByRole('button', { name: /write my study guide/i }));

    expect(await screen.findByText(/AI service is down/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});
