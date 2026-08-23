export const MAX_ANSWER_TEXT_CHARS = 5000;
export const SHORT_ANSWER_ROWS = 2;
export const OPEN_ENDED_ROWS = 7;

export interface AnswerDraft {
  optionIndex: number | null;
  text: string;
}

export const EMPTY_DRAFT: AnswerDraft = { optionIndex: null, text: '' };
