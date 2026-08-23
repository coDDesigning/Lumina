export const ALL_TOPICS = 'All Topics';

export function topicOptions(topics: readonly string[]): string[] {
  const seen = new Set<string>([ALL_TOPICS.toLowerCase()]);
  const options = [ALL_TOPICS];

  for (const topic of topics) {
    const trimmed = topic.trim();
    const key = trimmed.toLowerCase();
    if (!trimmed || seen.has(key)) {
      continue;
    }
    seen.add(key);
    options.push(trimmed);
  }

  return options;
}
