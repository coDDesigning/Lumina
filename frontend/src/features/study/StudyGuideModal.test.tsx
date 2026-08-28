import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { studyGuideAPI } from '@/api/studyGuide';
import { userAPI } from '@/api/user';
import type { CreditStatus, StudyGuideGenerationResult } from '@/api/types';
import { CreditProvider } from '@/context/CreditContext';
import { StudyGuideModal } from './StudyGuideModal';

vi.mock('@/api/studyGuide', () => ({
  studyGuideAPI: { generate: vi.fn() },
}));

vi.mock('@/api/settings', () => ({
  settingsAPI: { get: vi.fn().mockResolvedValue({ summary_length: 'medium', detail_level: 'standard' }) },
}));

vi.mock('@/api/user', () => ({
  userAPI: { getCredits: vi.fn() },
}));

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: true, user: { id: 1 } }),
}));

const mockGenerate = vi.mocked(studyGuideAPI.generate);
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

const GUIDE: StudyGuideGenerationResult = {
  generated_output_id: 12,
  context_truncated: false,
  retrieval_narrowed: false,
  lowest_similarity: 0.41,
  highest_similarity: 0.88,
  chunks_used: 4,
  chunks_available: 10,
  study_guide: {
    title: 'Sorting Algorithms',
    summary: 'Sorting puts a sequence in order.',
    key_points: ['Merge sort is stable'],
    important_terms: [{ term: 'Stability', definition: 'Equal keys keep their order.' }],
    common_mistakes: [{ mistake: 'Assuming quicksort is stable', correction: 'It is not.' }],
    exam_tips: { lecture_based: ['Know the recurrences'], ai_suggestions: ['Practise tracing'] },
    difficulty: { level: 'Medium', reason: 'Mixed material' },
    estimated_study_time: '45 minutes',
    prerequisites: ['Algebra'],
    learning_objectives: ['Understand the basics'],
    coverage: { status: 'Partial', estimated_completeness: 40 },
    confidence_notes: '',
  },
};

function renderModal(readyDocumentCount = 3) {
  const onClose = vi.fn();
  const onGenerated = vi.fn();
  render(
    <CreditProvider>
      <StudyGuideModal
        courseId={1}
        courseName="Algorithms"
        topics={['Sorting', 'Graphs']}
        readyDocumentCount={readyDocumentCount}
        onClose={onClose}
        onGenerated={onGenerated}
      />
    </CreditProvider>,
  );
  return { onClose, onGenerated, person: userEvent.setup() };
}

function useClipboard(writeText: ReturnType<typeof vi.fn>) {
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
    writable: true,
  });
  return writeText;
}

async function writeGuide() {
  const rendered = renderModal();
  await rendered.person.click(
    await screen.findByRole('button', { name: /write my study guide/i }),
  );
  return rendered;
}

beforeEach(() => {
  mockGetCredits.mockResolvedValue(UNMETERED);
  mockGenerate.mockResolvedValue(GUIDE);
});

describe('asking for a guide', () => {
  it('says what a study guide is before anything is generated', async () => {
    renderModal();

    expect(await screen.findByText(/what matters, what it means/i)).toBeInTheDocument();
  });

  it('refuses to write a guide out of nothing, and says why', async () => {
    renderModal(0);

    expect(await screen.findByText('There is nothing to work from yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /write my study guide/i })).toBeDisabled();
  });

  it('sends every choice the student made, not just the format', async () => {
    const { person } = renderModal();

    await person.selectOptions(await screen.findByLabelText(/What kind of guide/), 'exam_tips');
    await person.selectOptions(screen.getByLabelText(/Which topic/), 'Graphs');
    await person.selectOptions(screen.getByLabelText(/How long/), 'long');
    await person.selectOptions(screen.getByLabelText(/How deep/), 'detailed');
    await person.selectOptions(screen.getByLabelText(/What it is for/), 'exam_focused');
    await person.click(screen.getByRole('button', { name: /write my study guide/i }));

    await waitFor(() => expect(mockGenerate).toHaveBeenCalled());
    expect(mockGenerate.mock.calls[0][1]).toMatchObject({
      summary_format: 'exam_tips',
      topic_focus: 'Graphs',
      summary_length: 'long',
      detail_level: 'detailed',
      summary_mode: 'exam_focused',
    });
  });

  it('leaves the profile out unless the student opts in', async () => {
    const { person } = renderModal();

    await person.click(await screen.findByRole('checkbox', { name: /study profile/i }));
    await person.click(screen.getByRole('button', { name: /write my study guide/i }));

    await waitFor(() => expect(mockGenerate).toHaveBeenCalled());
    expect(mockGenerate.mock.calls[0][1]).toMatchObject({ use_profile_knowledge: true });
  });
});

describe('while the guide is being written', () => {
  it('says what it is doing and offers a way out', async () => {
    mockGenerate.mockReturnValue(new Promise(() => {}));
    await writeGuide();

    expect(await screen.findByRole('button', { name: /cancel/i })).toBeInTheDocument();
  });

  it('drops back to the setup when the student cancels', async () => {
    mockGenerate.mockReturnValue(new Promise(() => {}));
    const { person } = await writeGuide();

    await person.click(await screen.findByRole('button', { name: /cancel/i }));

    expect(
      await screen.findByRole('button', { name: /write my study guide/i }),
    ).toBeInTheDocument();
  });
});

describe('once the guide arrives', () => {
  it('shows the guide and tells the caller which output it was', async () => {
    const { onGenerated } = await writeGuide();

    expect(await screen.findByText('Sorting puts a sequence in order.')).toBeInTheDocument();
    await waitFor(() => expect(onGenerated).toHaveBeenCalledWith(12));
  });

  it('copies the guide as markdown and says so', async () => {
    const { person } = await writeGuide();
    const writeText = useClipboard(vi.fn().mockResolvedValue(undefined));

    await person.click(await screen.findByRole('button', { name: /^copy$/i }));

    expect(await screen.findByRole('button', { name: /copied/i })).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('Sorting Algorithms'));
  });

  it('admits a copy that did not work rather than claiming it did', async () => {
    const { person } = await writeGuide();
    useClipboard(vi.fn().mockRejectedValue(new Error('denied')));

    await person.click(await screen.findByRole('button', { name: /^copy$/i }));

    expect(await screen.findByRole('button', { name: /copy failed/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /copied/i })).toBeNull();
  });

  it('offers the guide as a file named for the course', async () => {
    const createObjectURL = vi.fn().mockReturnValue('blob:guide');
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = vi.fn();
    const { person } = await writeGuide();

    await person.click(await screen.findByRole('button', { name: /download/i }));

    expect(createObjectURL).toHaveBeenCalled();
  });

  it('goes back to the setup to write another', async () => {
    const { person } = await writeGuide();

    await person.click(await screen.findByRole('button', { name: /make another/i }));

    expect(
      await screen.findByRole('button', { name: /write my study guide/i }),
    ).toBeInTheDocument();
    expect(screen.queryByText('Sorting puts a sequence in order.')).toBeNull();
  });
});

describe('when the guide cannot be written', () => {
  it('names a provider outage rather than blaming the material', async () => {
    mockGenerate.mockRejectedValue(new APIError(503, { detail: 'down' }, 'provider_unavailable'));
    await writeGuide();

    expect(await screen.findByText('The AI service is down')).toBeInTheDocument();
  });

  it('offers a broader topic when nothing covers the one asked for', async () => {
    mockGenerate.mockRejectedValue(new APIError(409, { detail: 'no' }, 'no_relevant_material'));
    await writeGuide();

    expect(await screen.findByText('Nothing on that topic')).toBeInTheDocument();
  });

  it('withholds the retry when trying again cannot help', async () => {
    mockGenerate.mockRejectedValue(new APIError(409, { detail: 'no' }, 'no_relevant_material'));
    await writeGuide();

    await screen.findByText('Nothing on that topic');
    expect(screen.getByRole('button', { name: /try again/i })).toBeDisabled();
  });

  it('names a lost connection as being offline', async () => {
    mockGenerate.mockRejectedValue(new TypeError('Failed to fetch'));
    await writeGuide();

    expect(await screen.findByText('You are offline')).toBeInTheDocument();
  });
});
