import type { RetrievedContext } from '@/api/types';

export function provenanceParts(context: RetrievedContext): string[] {
  const parts = [`Read ${context.chunks_used} of ${context.chunks_available} passages`];

  if (context.lowest_similarity != null && context.highest_similarity != null) {
    parts.push(
      `match ${context.lowest_similarity.toFixed(2)}–${context.highest_similarity.toFixed(2)}`,
    );
  }
  if (context.retrieval_narrowed) {
    parts.push('narrowed to the passages about your question');
  }
  if (context.context_truncated) {
    parts.push('some selected passages did not fit and were left out');
  }
  if (context.profile_knowledge_used) {
    const notes = context.profile_knowledge_items_used;
    parts.push(
      notes != null && notes > 0
        ? `plus ${notes} notes from your profile`
        : 'plus notes from your profile',
    );
  }

  return parts;
}
