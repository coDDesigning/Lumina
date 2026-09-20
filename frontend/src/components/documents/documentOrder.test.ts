import { describe, expect, it } from 'vitest';
import { compareNames, sortByName, sortByNewest } from './documentOrder';

describe('compareNames', () => {
  it('orders numeric segments numerically rather than lexically', () => {
    expect(compareNames('Week 2', 'Week 10')).toBeLessThan(0);
  });

  it('ignores case', () => {
    expect(compareNames('alpha', 'ALPHA')).toBe(0);
  });
});

describe('sortByName', () => {
  it('sorts case-insensitively with natural numeric order', () => {
    const names = ['Week 10', 'week 2', 'Week 1'];
    expect(sortByName(names, (name) => name)).toEqual(['Week 1', 'week 2', 'Week 10']);
  });

  it('sorts mixed-case Turkish names case-insensitively', () => {
    const names = ['İstatistik', 'Hafta', 'Çizge', 'Bilgi'];
    expect(sortByName(names, (name) => name)).toEqual(['Bilgi', 'Çizge', 'Hafta', 'İstatistik']);
  });

  it('is stable for names that compare equal', () => {
    const items = [
      { id: 'a', name: 'same' },
      { id: 'b', name: 'SAME' },
      { id: 'c', name: 'Same' },
    ];
    expect(sortByName(items, (item) => item.name).map((item) => item.id)).toEqual([
      'a',
      'b',
      'c',
    ]);
  });

  it('returns a new array and leaves the input untouched', () => {
    const input = ['b', 'a'];
    const sorted = sortByName(input, (name) => name);
    expect(sorted).not.toBe(input);
    expect(sorted).toEqual(['a', 'b']);
    expect(input).toEqual(['b', 'a']);
  });
});

describe('sortByNewest', () => {
  it('orders the most recently created item first', () => {
    const items = [
      { id: 'old', createdAt: '2026-01-01T00:00:00Z' },
      { id: 'new', createdAt: '2026-03-01T00:00:00Z' },
      { id: 'mid', createdAt: '2026-02-01T00:00:00Z' },
    ];
    expect(sortByNewest(items, (item) => item.createdAt).map((item) => item.id)).toEqual([
      'new',
      'mid',
      'old',
    ]);
  });

  it('returns a new array and leaves the input untouched', () => {
    const input = [
      { id: 'a', createdAt: '2026-01-01T00:00:00Z' },
      { id: 'b', createdAt: '2026-02-01T00:00:00Z' },
    ];
    const sorted = sortByNewest(input, (item) => item.createdAt);
    expect(sorted).not.toBe(input);
    expect(input.map((item) => item.id)).toEqual(['a', 'b']);
  });
});
