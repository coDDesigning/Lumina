import { useCallback, useEffect, useRef, useState } from 'react';
import { coursesAPI } from '@/api/courses';
import { describeGenerationError } from '@/api/errors';
import type { GenerationFailure } from '@/api/errors';
import type { SuggestedTopic } from '@/api/types';

export interface SyllabusTopicSuggestionsState {
  suggestions: SuggestedTopic[];
  isPending: boolean;
  error: GenerationFailure | null;
  suggest: (text: string) => void;
  clear: () => void;
}

interface RequestState {
  requestedFor: string | null;
  suggestions: SuggestedTopic[];
  isPending: boolean;
  error: GenerationFailure | null;
}

const EMPTY_STATE: RequestState = {
  requestedFor: null,
  suggestions: [],
  isPending: false,
  error: null,
};

export function useSyllabusTopicSuggestions(currentText: string): SyllabusTopicSuggestionsState {
  const [state, setState] = useState<RequestState>(EMPTY_STATE);

  const isMounted = useRef(true);
  const requestId = useRef(0);

  useEffect(() => {
    isMounted.current = true;
    return () => {
      isMounted.current = false;
    };
  }, []);

  const suggest = useCallback((text: string) => {
    const id = ++requestId.current;
    const requestedFor = text.trim();
    setState({ requestedFor, suggestions: [], isPending: true, error: null });

    coursesAPI
      .suggestSyllabusTopics(text)
      .then((topics) => {
        if (!isMounted.current || requestId.current !== id) {
          return;
        }
        setState({ requestedFor, suggestions: topics, isPending: false, error: null });
      })
      .catch((caught: unknown) => {
        if (!isMounted.current || requestId.current !== id) {
          return;
        }
        setState({
          requestedFor,
          suggestions: [],
          isPending: false,
          error: describeGenerationError(caught, "Your syllabus topics couldn't be suggested."),
        });
      });
  }, []);

  const clear = useCallback(() => {
    requestId.current += 1;
    setState(EMPTY_STATE);
  }, []);

  const isCurrent = state.requestedFor !== null && state.requestedFor === currentText.trim();

  return {
    suggestions: isCurrent ? state.suggestions : [],
    isPending: isCurrent && state.isPending,
    error: isCurrent ? state.error : null,
    suggest,
    clear,
  };
}
