import { describe, expect, it } from 'vitest';
import type { RetrievedContext } from '@/api/types';
import { provenanceParts } from './provenanceParts';

function context(overrides: Partial<RetrievedContext> = {}): RetrievedContext {
  return {
    chunks_used: 4,
    chunks_available: 9,
    context_truncated: false,
    retrieval_narrowed: false,
    lowest_similarity: null,
    highest_similarity: null,
    ...overrides,
  };
}

describe('what the material report says', () => {
  it('always says how much of the course was read', () => {
    expect(provenanceParts(context())).toContain('Read 4 of 9 passages');
  });

  it('reports the match range only when both ends are known', () => {
    expect(provenanceParts(context({ lowest_similarity: 0.41, highest_similarity: 0.88 }))).toContain(
      'match 0.41–0.88',
    );
    expect(provenanceParts(context({ lowest_similarity: 0.41 })).join(' ')).not.toMatch(/match/);
  });

  it('separates narrowing from truncation, which are different things', () => {
    const narrowed = provenanceParts(context({ retrieval_narrowed: true }));
    const truncated = provenanceParts(context({ context_truncated: true }));

    expect(narrowed).toContain('narrowed to the passages about your question');
    expect(narrowed.join(' ')).not.toMatch(/did not fit/);
    expect(truncated.join(' ')).toMatch(/did not fit/);
  });

  it('counts the profile notes it actually used', () => {
    expect(
      provenanceParts(
        context({ profile_knowledge_used: true, profile_knowledge_items_used: 3 }),
      ),
    ).toContain('plus 3 notes from your profile');
  });

  it('claims no count when a stored row recorded none', () => {
    const parts = provenanceParts(
      context({ profile_knowledge_used: true, profile_knowledge_items_used: 0 }),
    );

    expect(parts.join(' ')).toMatch(/notes from your profile/);
    expect(parts.join(' ')).not.toMatch(/plus 0 notes/);
  });

  it('prints no placeholder when the count is missing altogether', () => {
    const parts = provenanceParts(context({ profile_knowledge_used: true }));

    expect(parts.join(' ')).toMatch(/notes from your profile/);
    expect(parts.join(' ')).not.toMatch(/undefined|null|plus 0/);
  });

  it('says nothing about the profile when none of it was used', () => {
    expect(provenanceParts(context()).join(' ')).not.toMatch(/profile/);
  });
});
