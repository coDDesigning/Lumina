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
  studyGuideAPI: { generate: vi.fn() },
}));

vi.mock('@/api/quiz', () => ({
  quizAPI: { generate: vi.fn(), submitAttempt: vi.fn() },
}));

vi.mock('@/api/flashcards', () => ({
  flashcardsAPI: { generate: vi.fn() },
}));

vi.mock('@/api/user', () => ({
  userAPI: { getCredits: vi.fn() },
}));

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: true, user: { id: 1 } }),
}));

const mockStudyGuideGenerate = vi.mocked(studyGuideAPI.generate);
const mockQuizGenerate = vi.mocked(quizAPI.generate);
const mockFlashcardGenerate = vi.mocked(flashcardsAPI.generate);
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
  });

  describe('StudyGuideModal', () => {
    it('defaults toggle to false, explains course material primacy, and submits false when untouched', async () => {
      mockStudyGuideGenerate.mockResolvedValueOnce({
        generated_output_id: 1,
        context_truncated: false,
        retrieval_narrowed: false,
        lowest_similarity: 0.5,
        highest_similarity: 0.9,
        chunks_used: 2,
        chunks_available: 5,
        study_guide: {
          title: 'Guide Title',
          summary: 'Guide Summary',
          key_points: [],
          important_terms: [],
          common_mistakes: [],
          exam_tips: { lecture_based: [], ai_suggestions: [] },
          difficulty: { level: 'Easy', reason: 'Basic' },
          estimated_study_time: '15m',
          prerequisites: [],
          learning_objectives: [],
          coverage: { status: 'Complete', estimated_completeness: 100 },
          confidence_notes: '',
        },
      });

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
        expect(mockStudyGuideGenerate).toHaveBeenCalledWith(
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
      mockStudyGuideGenerate.mockResolvedValueOnce({
        generated_output_id: 2,
        context_truncated: false,
        retrieval_narrowed: false,
        lowest_similarity: 0.6,
        highest_similarity: 0.95,
        chunks_used: 3,
        chunks_available: 5,
        study_guide: {
          title: 'Guide Title 2',
          summary: 'Guide Summary 2',
          key_points: [],
          important_terms: [],
          common_mistakes: [],
          exam_tips: { lecture_based: [], ai_suggestions: [] },
          difficulty: { level: 'Medium', reason: 'Moderate' },
          estimated_study_time: '30m',
          prerequisites: [],
          learning_objectives: [],
          coverage: { status: 'Complete', estimated_completeness: 100 },
          confidence_notes: '',
        },
      });

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
        expect(mockStudyGuideGenerate).toHaveBeenCalledWith(
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
      mockQuizGenerate.mockResolvedValueOnce({
        generated_output_id: 10,
        quiz: {
          quiz_id: 10,
          course_id: 1,
          title: 'Quiz 1',
          created_at: '2026-08-21T10:00:00Z',
          user_id: 1,
          model_used: null,
          generation_settings: null,
          generation_context: null,
          questions: [],
        },
        context_truncated: false,
        retrieval_narrowed: false,
        lowest_similarity: 0.5,
        highest_similarity: 0.9,
        chunks_used: 2,
        chunks_available: 5,
      });

      render(
        <CreditProvider>
          <QuizModal
            courseId={1}
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
        screen.getByRole('button', { name: /start the quiz/i }),
      );

      await waitFor(() => {
        expect(mockQuizGenerate).toHaveBeenCalledWith(
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
      mockQuizGenerate.mockResolvedValueOnce({
        generated_output_id: 11,
        quiz: {
          quiz_id: 11,
          course_id: 1,
          title: 'Quiz 2',
          created_at: '2026-08-21T10:00:00Z',
          user_id: 1,
          model_used: null,
          generation_settings: null,
          generation_context: null,
          questions: [],
        },
        context_truncated: false,
        retrieval_narrowed: false,
        lowest_similarity: 0.5,
        highest_similarity: 0.9,
        chunks_used: 2,
        chunks_available: 5,
      });

      render(
        <CreditProvider>
          <QuizModal
            courseId={1}
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
        screen.getByRole('button', { name: /start the quiz/i }),
      );

      await waitFor(() => {
        expect(mockQuizGenerate).toHaveBeenCalledWith(
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
      mockFlashcardGenerate.mockResolvedValueOnce({
        context_truncated: false,
        retrieval_narrowed: true,
        lowest_similarity: 0.45,
        highest_similarity: 0.85,
        chunks_used: 2,
        chunks_available: 5,
        flashcards: {
          deck_title: 'Data Structures Flashcards',
          card_count: 1,
          flashcards: [
            {
              card_number: 1,
              front: 'What is a stack?',
              back: 'LIFO structure',
              difficulty: 'Easy',
            },
          ],
        },
      });

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
        expect(mockFlashcardGenerate).toHaveBeenCalledWith(
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
      mockFlashcardGenerate.mockResolvedValueOnce({
        context_truncated: false,
        retrieval_narrowed: true,
        lowest_similarity: 0.45,
        highest_similarity: 0.85,
        chunks_used: 2,
        chunks_available: 5,
        flashcards: {
          deck_title: 'Data Structures Flashcards',
          card_count: 1,
          flashcards: [
            {
              card_number: 1,
              front: 'What is a queue?',
              back: 'FIFO structure',
              difficulty: 'Easy',
            },
          ],
        },
      });

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
        expect(mockFlashcardGenerate).toHaveBeenCalledWith(
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
