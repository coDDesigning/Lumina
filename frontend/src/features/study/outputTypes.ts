const OUTPUT_TYPE_LABELS: Record<string, string> = {
  study_guide: 'Study guide',
  last_minute_review: 'Last-minute review',
  flashcards: 'Flashcards',
  flashcard: 'Flashcards',
  quiz: 'Practice quiz',
  reverse_quiz: 'Reverse quiz',
  exam_topic_analysis: 'Exam source analysis',
  exam_plan: 'Exam plan',
  exam_roadmap: 'Exam roadmap',
  exam_mock_exam: 'Mock exam',
  exam_review_sheet: 'Review sheet',
  exam_topic_guide: 'Topic guide',
  exam_topic_summary: 'Topic summary',
  exam_topic_practice: 'Topic practice',
  exam_topic_exam: 'Topic exam',
  exam_similar_questions: 'Similar questions',
};

const UNLISTED_OUTPUT_TYPES: ReadonlySet<string> = new Set(['reverse_quiz', 'exam_topic_analysis']);

export const QUIZ_SHAPED_OUTPUT_TYPES: ReadonlySet<string> = new Set([
  'quiz',
  'exam_mock_exam',
  'exam_topic_practice',
  'exam_topic_exam',
  'exam_similar_questions',
]);

export function outputTypeLabel(outputType: string): string {
  const label = OUTPUT_TYPE_LABELS[outputType];
  if (label) {
    return label;
  }
  const words = outputType.replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function isListedOutputType(outputType: string): boolean {
  return !UNLISTED_OUTPUT_TYPES.has(outputType);
}
