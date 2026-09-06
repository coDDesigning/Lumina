/**
 * The bounds a generated question count must clear before it is worth sending.
 *
 * The backend owns the rule (`MIN_QUIZ_QUESTIONS`/`MAX_QUIZ_QUESTIONS`), and it
 * refuses an out-of-range count with a raw validation string no student should
 * read. Mirroring it here is what turns that into an inline instruction before
 * the click, so the field is held as the text the student typed and parsed
 * once -- a number input's partial states ('', '-') are text, and writing NaN
 * into a controlled value is a React warning rather than a validation result.
 */

export const MIN_QUESTION_COUNT = 1;
export const MAX_QUESTION_COUNT = 20;

export interface ParsedQuestionCount {
  /** NaN when the field cannot be read as a count; never sent in that state. */
  value: number;
  /** What the student is told, or null when the count is usable. */
  error: string | null;
}

export function parseQuestionCount(raw: string): ParsedQuestionCount {
  const trimmed = raw.trim();
  if (trimmed === '') {
    return { value: Number.NaN, error: 'Enter how many questions you want.' };
  }

  const value = Number(trimmed);
  if (!Number.isInteger(value)) {
    return { value: Number.NaN, error: 'Enter a whole number of questions.' };
  }
  if (value < MIN_QUESTION_COUNT || value > MAX_QUESTION_COUNT) {
    return {
      value,
      error: `Choose between ${MIN_QUESTION_COUNT} and ${MAX_QUESTION_COUNT} questions.`,
    };
  }

  return { value, error: null };
}
