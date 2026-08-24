import { afterEach, describe, expect, it, vi } from 'vitest';
import { shuffle } from './shuffle';
import { ALL_TOPICS, topicOptions } from './topicOptions';

function seededRandom(seed: number) {
  let state = seed;
  return () => {
    state = (state * 1664525 + 1013904223) % 4294967296;
    return state / 4294967296;
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('shuffle', () => {
  it('leaves the caller’s array untouched and keeps every card', () => {
    const original = ['a', 'b', 'c', 'd', 'e'];
    const result = shuffle(original);

    expect(original).toEqual(['a', 'b', 'c', 'd', 'e']);
    expect([...result].sort()).toEqual(['a', 'b', 'c', 'd', 'e']);
  });

  it('walks the array from the end, swapping with a random earlier index', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0);

    expect(shuffle(['a', 'b', 'c', 'd'])).toEqual(['b', 'c', 'd', 'a']);
  });

  it('reaches every permutation at close to equal frequency', () => {
    vi.spyOn(Math, 'random').mockImplementation(seededRandom(20260823));

    const counts = new Map<string, number>();
    for (let run = 0; run < 24000; run += 1) {
      const key = shuffle(['a', 'b', 'c', 'd']).join('');
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }

    expect(counts.size).toBe(24);
    for (const count of counts.values()) {
      expect(count).toBeGreaterThan(750);
      expect(count).toBeLessThan(1250);
    }
  });
});

describe('topicOptions', () => {
  it('offers the every-topic choice first', () => {
    expect(topicOptions(['Vectors'])).toEqual([ALL_TOPICS, 'Vectors']);
  });

  it('drops the blanks a trailing comma leaves behind', () => {
    expect(topicOptions(['Vectors', '', '   ', 'Matrices'])).toEqual([
      ALL_TOPICS,
      'Vectors',
      'Matrices',
    ]);
  });

  it('keeps one entry per topic however it was cased', () => {
    expect(topicOptions(['Vectors', 'vectors', 'VECTORS'])).toEqual([ALL_TOPICS, 'Vectors']);
  });

  it('does not repeat the every-topic choice when the course lists it too', () => {
    expect(topicOptions(['All Topics', 'Vectors'])).toEqual([ALL_TOPICS, 'Vectors']);
  });
});
