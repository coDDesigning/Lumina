import { describe, expect, it } from 'vitest';
import { MAX_QUESTION_COUNT, MIN_QUESTION_COUNT, parseQuestionCount } from './questionCount';

describe('parseQuestionCount', () => {
  it.each([
    ['5', 5],
    ['1', MIN_QUESTION_COUNT],
    ['20', MAX_QUESTION_COUNT],
    [' 7 ', 7],
  ])('accepts %s', (raw, expected) => {
    const parsed = parseQuestionCount(raw);

    expect(parsed.error).toBeNull();
    expect(parsed.value).toBe(expected);
  });

  // Every partial state a number input can hold, plus both bounds. A count the
  // server would refuse must never reach it, because its refusal is a raw
  // validation string rather than an instruction.
  it.each([
    ['', 'Enter how many questions you want.'],
    ['   ', 'Enter how many questions you want.'],
    ['-', 'Enter a whole number of questions.'],
    ['abc', 'Enter a whole number of questions.'],
    ['2.5', 'Enter a whole number of questions.'],
    ['0', `Choose between ${MIN_QUESTION_COUNT} and ${MAX_QUESTION_COUNT} questions.`],
    ['-3', `Choose between ${MIN_QUESTION_COUNT} and ${MAX_QUESTION_COUNT} questions.`],
    ['21', `Choose between ${MIN_QUESTION_COUNT} and ${MAX_QUESTION_COUNT} questions.`],
  ])('refuses %s', (raw, expected) => {
    expect(parseQuestionCount(raw).error).toBe(expected);
  });
});
