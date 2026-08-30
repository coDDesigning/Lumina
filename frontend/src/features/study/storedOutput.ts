import type {
  FlashcardGenerationResponse,
  GeneratedFlashcard,
  GeneratedOutputDetail,
  QuizView,
  RetrievedContext,
  StudyGuideGenerationResult,
  StudyGuideResponse,
} from '@/api/types';

export function tryParseJson(value: unknown): unknown {
  if (typeof value === 'string') {
    try {
      return JSON.parse(value);
    } catch {
      return value;
    }
  }
  return value;
}

function isCited(value: unknown): boolean {
  return (
    typeof value === 'string' ||
    (typeof value === 'object' &&
      value !== null &&
      typeof (value as { text?: unknown }).text === 'string')
  );
}

export function isRenderableStudyGuide(content: unknown): content is StudyGuideResponse {
  const parsed = tryParseJson(content);
  if (typeof parsed !== 'object' || parsed === null) {
    return false;
  }
  const candidate = parsed as Record<string, unknown>;
  const difficulty = candidate.difficulty as Record<string, unknown> | undefined;
  const coverage = candidate.coverage as Record<string, unknown> | undefined;
  const examTips = candidate.exam_tips as Record<string, unknown> | undefined;

  return (
    typeof candidate.title === 'string' &&
    isCited(candidate.summary) &&
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

export function extractFlashcards(content: unknown): GeneratedFlashcard[] | null {
  const parsed = tryParseJson(content);
  if (typeof parsed !== 'object' || parsed === null) {
    return null;
  }
  if (Array.isArray(parsed)) {
    const valid = parsed.every(
      (item) =>
        typeof item === 'object' &&
        item !== null &&
        typeof (item as Record<string, unknown>).front === 'string' &&
        typeof (item as Record<string, unknown>).back === 'string',
    );
    return valid ? (parsed as GeneratedFlashcard[]) : null;
  }
  const candidate = parsed as Record<string, unknown>;
  if (Array.isArray(candidate.flashcards)) {
    const valid = candidate.flashcards.every(
      (item) =>
        typeof item === 'object' &&
        item !== null &&
        typeof (item as Record<string, unknown>).front === 'string' &&
        typeof (item as Record<string, unknown>).back === 'string',
    );
    return valid ? (candidate.flashcards as GeneratedFlashcard[]) : null;
  }
  return null;
}

export function isRenderableFlashcards(content: unknown): content is FlashcardGenerationResponse {
  const parsed = tryParseJson(content);
  if (typeof parsed !== 'object' || parsed === null) {
    return false;
  }
  const candidate = parsed as Record<string, unknown>;

  return (
    (typeof candidate.deck_title === 'string' || candidate.deck_title === undefined) &&
    (typeof candidate.card_count === 'number' || candidate.card_count === undefined) &&
    Array.isArray(candidate.flashcards) &&
    (candidate.flashcards.length === 0 ||
      (typeof candidate.flashcards[0] === 'object' &&
        candidate.flashcards[0] !== null &&
        'front' in candidate.flashcards[0] &&
        'back' in candidate.flashcards[0]))
  );
}

export function isRenderableQuiz(content: unknown): content is QuizView {
  const parsed = tryParseJson(content);
  if (typeof parsed !== 'object' || parsed === null) {
    return false;
  }
  const candidate = parsed as Record<string, unknown>;

  return (
    typeof candidate.title === 'string' &&
    (typeof candidate.quiz_id === 'number' || candidate.quiz_id === undefined) &&
    Array.isArray(candidate.questions) &&
    (candidate.questions.length === 0 ||
      (typeof candidate.questions[0] === 'object' &&
        candidate.questions[0] !== null &&
        'question' in candidate.questions[0]))
  );
}

export function extractQuiz(content: unknown): QuizView | null {
  const parsed = tryParseJson(content);
  if (isRenderableQuiz(parsed)) {
    return parsed as QuizView;
  }
  return null;
}

export function studyGuideContext(output: GeneratedOutputDetail): RetrievedContext | null {
  const context = output.generation_context;
  if (!context) {
    return null;
  }
  return {
    context_truncated: context.truncated,
    chunks_used: context.chunks_used,
    chunks_available: context.chunks_available,
    retrieval_narrowed: context.chunks_used < context.chunks_available,
    lowest_similarity: context.lowest_similarity ?? null,
    highest_similarity: context.highest_similarity ?? null,
    profile_knowledge_used: context.profile_knowledge_used ?? false,
    profile_knowledge_items_used: context.profile_knowledge_items_used ?? null,
  };
}

export function asExportableStudyGuide(
  output: GeneratedOutputDetail,
): StudyGuideGenerationResult | null {
  const { content } = output;
  if (
    (output.output_type !== 'study_guide' && output.output_type !== 'last_minute_review') ||
    !isRenderableStudyGuide(content)
  ) {
    return null;
  }
  const guide =
    typeof content === 'string'
      ? (tryParseJson(content) as StudyGuideResponse)
      : (content as StudyGuideResponse);
  const context = studyGuideContext(output);
  return {
    study_guide: guide,
    generated_output_id: output.id,
    context_truncated: context?.context_truncated ?? false,
    chunks_used: context?.chunks_used ?? 0,
    chunks_available: context?.chunks_available ?? 0,
    retrieval_narrowed: context?.retrieval_narrowed ?? false,
    lowest_similarity: context?.lowest_similarity ?? null,
    highest_similarity: context?.highest_similarity ?? null,
    profile_knowledge_used: context?.profile_knowledge_used ?? false,
    profile_knowledge_items_used: context?.profile_knowledge_items_used ?? null,
  };
}
