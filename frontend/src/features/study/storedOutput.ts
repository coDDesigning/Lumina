import type {
  FlashcardGenerationResponse,
  QuizView,
  StudyGuideResponse,
} from '@/api/types';

export function isRenderableStudyGuide(content: unknown): content is StudyGuideResponse {
  if (typeof content !== 'object' || content === null) {
    return false;
  }
  const candidate = content as Record<string, unknown>;
  const difficulty = candidate.difficulty as Record<string, unknown> | undefined;
  const coverage = candidate.coverage as Record<string, unknown> | undefined;
  const examTips = candidate.exam_tips as Record<string, unknown> | undefined;

  return (
    typeof candidate.title === 'string' &&
    typeof candidate.summary === 'string' &&
    typeof candidate.estimated_study_time === 'string' &&
    Array.isArray(candidate.key_points) &&
    Array.isArray(candidate.important_terms) &&
    Array.isArray(candidate.common_mistakes) &&
    Array.isArray(candidate.prerequisites) &&
    Array.isArray(candidate.learning_objectives) &&
    typeof difficulty?.level === 'string' &&
    typeof coverage?.status === 'string' &&
    Array.isArray(examTips?.lecture_based) &&
    Array.isArray(examTips?.ai_suggestions)
  );
}

export function isRenderableFlashcards(content: unknown): content is FlashcardGenerationResponse {
  if (typeof content !== 'object' || content === null) {
    return false;
  }
  const candidate = content as Record<string, unknown>;

  return (
    typeof candidate.deck_title === 'string' &&
    typeof candidate.card_count === 'number' &&
    Array.isArray(candidate.flashcards) &&
    (candidate.flashcards.length === 0 ||
      (typeof candidate.flashcards[0] === 'object' &&
        candidate.flashcards[0] !== null &&
        'front' in candidate.flashcards[0] &&
        'back' in candidate.flashcards[0]))
  );
}

export function isRenderableQuiz(content: unknown): content is QuizView {
  if (typeof content !== 'object' || content === null) {
    return false;
  }
  const candidate = content as Record<string, unknown>;

  return (
    typeof candidate.title === 'string' &&
    typeof candidate.quiz_id === 'number' &&
    Array.isArray(candidate.questions) &&
    (candidate.questions.length === 0 ||
      (typeof candidate.questions[0] === 'object' &&
        candidate.questions[0] !== null &&
        'question' in candidate.questions[0]))
  );
}
