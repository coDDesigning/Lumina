import { describe, expect, it } from 'vitest';
import { formatStudyTime } from './formatStudyTime';

describe('formatStudyTime', () => {
  it('reports seconds below a minute', () => {
    expect(formatStudyTime(1)).toBe('1s');
    expect(formatStudyTime(59)).toBe('59s');
  });

  it('reports whole minutes below an hour', () => {
    expect(formatStudyTime(60)).toBe('1m');
    expect(formatStudyTime(90)).toBe('1m');
    expect(formatStudyTime(3599)).toBe('59m');
  });

  it('reports hours and minutes past an hour', () => {
    expect(formatStudyTime(3600)).toBe('1h');
    expect(formatStudyTime(4320)).toBe('1h 12m');
    expect(formatStudyTime(7200)).toBe('2h');
    expect(formatStudyTime(7260)).toBe('2h 1m');
  });

  it('has nothing to report for zero or nonsense', () => {
    expect(formatStudyTime(0)).toBeNull();
    expect(formatStudyTime(-30)).toBeNull();
    expect(formatStudyTime(Number.NaN)).toBeNull();
  });
});
