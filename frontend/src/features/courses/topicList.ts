export const MAX_TOPICS = 50;

function topicKey(topic: string): string {
  return topic.trim().toLowerCase();
}

export function addTopics(current: readonly string[], added: readonly string[]): string[] {
  const merged: string[] = [];
  const seen = new Set<string>();

  for (const topic of [...current, ...added]) {
    const key = topicKey(topic);
    if (!key || seen.has(key)) {
      continue;
    }
    seen.add(key);
    merged.push(topic);
  }

  return merged.slice(0, MAX_TOPICS);
}
