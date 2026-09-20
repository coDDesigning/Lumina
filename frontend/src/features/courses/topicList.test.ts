import { describe, expect, it } from 'vitest';
import { addTopics, MAX_TOPICS } from './topicList';

describe('addTopics', () => {
  it('adds a new topic to the current list', () => {
    expect(addTopics(['Hashing'], ['Sorting'])).toEqual(['Hashing', 'Sorting']);
  });

  it('drops an addition that differs from an existing topic only in case', () => {
    expect(addTopics(['Graph Traversal'], ['graph traversal'])).toEqual(['Graph Traversal']);
  });

  it('drops duplicates within the added list itself, keeping the first spelling', () => {
    expect(addTopics([], ['Graph Traversal', 'graph traversal', 'GRAPH TRAVERSAL'])).toEqual([
      'Graph Traversal',
    ]);
  });

  it('ignores case and surrounding whitespace when comparing', () => {
    expect(addTopics(['  Hashing  '], ['hashing'])).toEqual(['  Hashing  ']);
  });

  it('drops blank or whitespace-only entries', () => {
    expect(addTopics(['Hashing'], ['   ', ''])).toEqual(['Hashing']);
  });

  it('preserves the order topics were first seen in', () => {
    expect(addTopics(['Hashing'], ['Sorting', 'Graphs'])).toEqual([
      'Hashing',
      'Sorting',
      'Graphs',
    ]);
  });

  it('caps the result at 50 topics', () => {
    const current = Array.from({ length: 48 }, (_, index) => `Current ${index}`);
    const added = ['New A', 'New B', 'New C'];

    const result = addTopics(current, added);

    expect(result).toHaveLength(MAX_TOPICS);
    expect(result.slice(-2)).toEqual(['New A', 'New B']);
  });

  it('does not mutate either input array', () => {
    const current = ['Hashing'];
    const added = ['Sorting'];

    addTopics(current, added);

    expect(current).toEqual(['Hashing']);
    expect(added).toEqual(['Sorting']);
  });

  it('returns every current topic unchanged when nothing is added', () => {
    expect(addTopics(['Hashing', 'Sorting'], [])).toEqual(['Hashing', 'Sorting']);
  });
});
