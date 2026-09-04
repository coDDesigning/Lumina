import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { StudyGuideModal } from '@/features/study/StudyGuideModal';
import { QuizModal } from '@/features/study/quiz/QuizModal';
import { FlashcardModal } from '@/features/study/FlashcardModal';
import { CreditProvider } from '@/context/CreditContext';
import { studyGuideAPI } from '@/api/studyGuide';
import { quizAPI } from '@/api/quiz';
import { flashcardsAPI } from '@/api/flashcards';
import { userAPI } from '@/api/user';
import type { CreditStatus } from '@/api/types';

vi.mock('@/api/studyGuide', () => ({
  studyGuideAPI: { generate: vi.fn(), enqueue: vi.fn() },
}));

vi.mock('@/api/quiz', () => ({
  quizAPI: { enqueue: vi.fn() },
}));

vi.mock('@/api/flashcards', () => ({
  flashcardsAPI: { enqueue: vi.fn() },
}));

vi.mock('@/api/user', () => ({
  userAPI: { getCredits: vi.fn() },
}));

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: true, user: { id: 1 } }),
}));

const mockStudyGuideEnqueue = vi.mocked(studyGuideAPI.enqueue);
const mockQuizEnqueue = vi.mocked(quizAPI.enqueue);
const mockFlashcardEnqueue = vi.mocked(flashcardsAPI.enqueue);
const mockGetCredits = vi.mocked(userAPI.getCredits);

function status(credits: number | null = 50): CreditStatus {
  return {
    credits,
    metering_enabled: true,
    email_verification_required: false,
    is_email_verified: true,
    monthly_grant: 50,
    balance_cap: 100,
    next_grant_at: '2026-09-01T00:00:00Z',
    generation_costs: {
      study_guide: 1,
      quiz: 1,
      quiz_open_ended: 2,
      flashcard: 1,
    },
  };
}

describe('Generation surfaces profile context toggle', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetCredits.mockResolvedValue(status(50));
    mockStudyGuideEnqueue.mockResolvedValue({ job_id: 1, status: 'queued' });
    mockFlashcardEnqueue.mockResolvedValue({ job_id: 2, status: 'queued' });
  });

  describe('StudyGuideModal', () => {
    it('defaults toggle to false, explains course material primacy, and submits false when untouched', async () => {
      render(
        <CreditProvider>
          <StudyGuideModal
            courseId={1}
            courseName="Algorithms"
            topics={['All Topics']}
            readyDocumentCount={2}
            onClose={vi.fn()}
          />
        </CreditProvider>,
      );

      const toggle = screen.getByRole('checkbox', { name: /use my study profile/i });
      expect(toggle).not.toBeChecked();

      expect(
        screen.getByText(/supporting context\. Your course material stays primary\./i),
      ).toBeInTheDocument();

      await userEvent.click(
        screen.getByRole('button', { name: /write my study guide/i }),
      );

      await waitFor(() => {
        expect(mockStudyGuideEnqueue).toHaveBeenCalledWith(
          1,
          expect.objectContaining({
            use_profile_knowledge: false,
            include_profile_context: false,
          }),
          expect.any(Object),
        );
      });
    });

    it('submits use_profile_knowledge: true when toggle is checked', async () => {
      render(
        <CreditProvider>
          <StudyGuideModal
            courseId={1}
            courseName="Algorithms"
            topics={['All Topics']}
            readyDocumentCount={2}
            onClose={vi.fn()}
          />
        </CreditProvider>,
      );

      const toggle = screen.getByRole('checkbox', { name: /use my study profile/i });
      await userEvent.click(toggle);
      expect(toggle).toBeChecked();

      await userEvent.click(
        screen.getByRole('button', { name: /write my study guide/i }),
      );

      await waitFor(() => {
        expect(mockStudyGuideEnqueue).toHaveBeenCalledWith(
          1,
          expect.objectContaining({
            use_profile_knowledge: true,
            include_profile_context: true,
          }),
          expect.any(Object),
        );
      });
    });
  });

  describe('QuizModal', () => {
    it('defaults toggle to false, explains course material primacy, and submits false when untouched', async () => {
      mockQuizEnqueue.mockResolvedValueOnce({ job_id: 10, status: 'queued' });

      render(
        <CreditProvider>
          <QuizModal
            courseId={1}
            topics={['All Topics']}
            readyDocumentCount={2}
            onQueued={vi.fn()}
            onClose={vi.fn()}
          />
        </CreditProvider>,
      );

      const toggle = screen.getByRole('checkbox', { name: /use my study profile/i });
      expect(toggle).not.toBeChecked();

      expect(
        screen.getByText(/supporting context\. Your course material stays primary\./i),
      ).toBeInTheDocument();

      await userEvent.click(
        screen.getByRole('button', { name: /start the quiz/i }),
      );

      await waitFor(() => {
        expect(mockQuizEnqueue).toHaveBeenCalledWith(
          1,
          expect.objectContaining({
            use_profile_knowledge: false,
            include_profile_context: false,
          }),
          expect.any(Object),
        );
      });
    });

    it('submits use_profile_knowledge: true when toggle is checked in QuizModal', async () => {
      mockQuizEnqueue.mockResolvedValueOnce({ job_id: 10, status: 'queued' });

      render(
        <CreditProvider>
          <QuizModal
            courseId={1}
            topics={['All Topics']}
            readyDocumentCount={2}
            onQueued={vi.fn()}
            onClose={vi.fn()}
          />
        </CreditProvider>,
      );

      const toggle = screen.getByRole('checkbox', { name: /use my study profile/i });
      await userEvent.click(toggle);
      expect(toggle).toBeChecked();

      await userEvent.click(
        screen.getByRole('button', { name: /start the quiz/i }),
      );

      await waitFor(() => {
        expect(mockQuizEnqueue).toHaveBeenCalledWith(
          1,
          expect.objectContaining({
            use_profile_knowledge: true,
            include_profile_context: true,
          }),
          expect.any(Object),
        );
      });
    });
  });

  describe('FlashcardModal', () => {
    it('defaults toggle to false, explains course material primacy, and submits false when untouched', async () => {
      render(
        <CreditProvider>
          <FlashcardModal
            courseId={1}
            courseName="Data Structures"
            readyDocumentCount={2}
            onClose={vi.fn()}
          />
        </CreditProvider>,
      );

      const toggle = screen.getByRole('checkbox', { name: /use my study profile/i });
      expect(toggle).not.toBeChecked();

      expect(
        screen.getByText(/supporting context\. Your course material stays primary\./i),
      ).toBeInTheDocument();

      await userEvent.click(screen.getByRole('button', { name: /make flashcards/i }));

      await waitFor(() => {
        expect(mockFlashcardEnqueue).toHaveBeenCalledWith(
          1,
          expect.objectContaining({
            use_profile_knowledge: false,
            include_profile_context: false,
          }),
          expect.any(Object),
        );
      });
    });

    it('submits use_profile_knowledge: true when toggle is checked in FlashcardModal', async () => {
      render(
        <CreditProvider>
          <FlashcardModal
            courseId={1}
            courseName="Data Structures"
            readyDocumentCount={2}
            onClose={vi.fn()}
          />
        </CreditProvider>,
      );

      const toggle = screen.getByRole('checkbox', { name: /use my study profile/i });
      await userEvent.click(toggle);
      expect(toggle).toBeChecked();

      await userEvent.click(screen.getByRole('button', { name: /make flashcards/i }));

      await waitFor(() => {
        expect(mockFlashcardEnqueue).toHaveBeenCalledWith(
          1,
          expect.objectContaining({
            use_profile_knowledge: true,
            include_profile_context: true,
          }),
          expect.any(Object),
        );
      });
    });
  });
});
