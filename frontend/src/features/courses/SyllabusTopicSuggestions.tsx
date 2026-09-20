import type { GenerationFailure } from '@/api/errors';
import type { SuggestedTopic } from '@/api/types';
import { ErrorState } from '@/ui/ErrorState';
import { TopicSuggestions } from './TopicSuggestions';
import styles from './SyllabusTopicSuggestions.module.css';

export interface SyllabusTopicSuggestionsProps {
  suggestions: readonly SuggestedTopic[];
  isPending: boolean;
  error: GenerationFailure | null;
  declared: readonly string[];
  onAdd: (topics: string[]) => void;
  onRetry: () => void;
}

export function SyllabusTopicSuggestions({
  suggestions,
  isPending,
  error,
  declared,
  onAdd,
  onRetry,
}: SyllabusTopicSuggestionsProps) {
  if (isPending) {
    return (
      <p className={styles.status} role="status">
        Reading topics from your syllabus…
      </p>
    );
  }

  if (error) {
    return (
      <ErrorState title={error.title} onRetry={onRetry}>
        {error.message}
      </ErrorState>
    );
  }

  return (
    <TopicSuggestions
      headingId="syllabus-topic-suggestions"
      title="Found in your syllabus"
      lede="Topics your syllabus lists that aren't in your topic list yet. Nothing is saved until you save the course."
      suggestions={suggestions.map((topic) => topic.name)}
      declared={declared}
      onAdd={onAdd}
    />
  );
}
