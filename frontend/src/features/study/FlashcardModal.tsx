import { useCallback, useEffect, useRef, useState } from 'react';
import { Layers, Sparkles } from 'lucide-react';
import { describeGenerationError, isAbortError, isInsufficientCredits } from '@/api/errors';
import type { GenerationFailure } from '@/api/errors';
import { flashcardsAPI } from '@/api/flashcards';
import type { FlashcardGenerationResult } from '@/api/types';
import CreditBalance from '@/components/credits/CreditBalance';
import CreditExhaustedNotice from '@/components/credits/CreditExhaustedNotice';
import { useCredits } from '@/context/CreditContext';
import { Button } from '@/ui/Button';
import { Checkbox } from '@/ui/Checkbox';
import { Dialog } from '@/ui/Dialog';
import { FlashcardDeck } from './FlashcardDeck';
import { GeneratingState, GenerationError, NoMaterialNotice, SetupPanel } from './GenerationStates';
import { Provenance } from './Provenance';

export interface FlashcardModalProps {
  courseId: number;
  courseName?: string;
  readyDocumentCount: number;
  onClose: () => void;
}

type FlashcardState =
  | { phase: 'idle' }
  | { phase: 'generating' }
  | { phase: 'success'; result: FlashcardGenerationResult }
  | { phase: 'error'; failure: GenerationFailure };

export function FlashcardModal({
  courseId,
  courseName,
  readyDocumentCount,
  onClose,
}: FlashcardModalProps) {
  const [state, setState] = useState<FlashcardState>({ phase: 'idle' });
  const [includeProfileContext, setIncludeProfileContext] = useState(false);
  const [elapsed, setElapsed] = useState(0);

  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    if (state.phase !== 'generating') {
      return;
    }
    setElapsed(0);
    const timer = setInterval(() => setElapsed((seconds) => seconds + 1), 1000);
    return () => clearInterval(timer);
  }, [state.phase]);

  const { refresh, canAfford, isMetered } = useCredits();
  const exhausted = isMetered && !canAfford('flashcard');
  const hasMaterial = readyDocumentCount > 0;

  const handleGenerate = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setState({ phase: 'generating' });

    try {
      const result = await flashcardsAPI.generate(
        courseId,
        {
          use_profile_knowledge: includeProfileContext,
          include_profile_context: includeProfileContext,
        },
        { signal: controller.signal },
      );
      setState({ phase: 'success', result });
      void refresh();
    } catch (caught) {
      if (isAbortError(caught)) {
        return;
      }
      const parsed = describeGenerationError(caught, 'flashcard');
      if (isInsufficientCredits(parsed)) {
        await refresh();
        setState({ phase: 'idle' });
        return;
      }
      setState({ phase: 'error', failure: parsed });
    }
  }, [courseId, includeProfileContext, refresh]);

  const handleCancel = useCallback(() => {
    abortRef.current?.abort();
    setState({ phase: 'idle' });
  }, []);

  const cards = state.phase === 'success' ? state.result.flashcards.flashcards : [];

  const footer = (() => {
    if (state.phase === 'generating') {
      return <Button onClick={handleCancel}>Cancel</Button>;
    }
    if (state.phase === 'success') {
      return (
        <>
          <Button onClick={onClose}>Done</Button>
          <Button variant="primary" onClick={() => void handleGenerate()} disabled={exhausted}>
            Make another set
          </Button>
        </>
      );
    }
    return (
      <>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="primary"
          onClick={() => void handleGenerate()}
          disabled={!hasMaterial || exhausted}
          icon={<Sparkles aria-hidden="true" />}
        >
          {state.phase === 'error' ? 'Start over' : 'Make flashcards'}
        </Button>
      </>
    );
  })();

  return (
    <Dialog
      open
      onClose={onClose}
      size="lg"
      title={state.phase === 'success' ? state.result.flashcards.deck_title : 'Flashcards'}
      description={courseName}
      mark={<Layers aria-hidden="true" />}
      footer={footer}
      spreadFooter
    >
      {state.phase === 'idle' ? (
        <SetupPanel lede="A deck of question-and-answer cards drawn from the material you have uploaded. Flip through them, shuffle, and go again.">
          {hasMaterial ? (
            <Checkbox
              label="Use my study profile"
              description="Adds your background as supporting context. Your course material stays primary."
              checked={includeProfileContext}
              onChange={(event) => setIncludeProfileContext(event.target.checked)}
            />
          ) : (
            <NoMaterialNotice what="A deck" />
          )}
          <CreditBalance source="flashcard" />
        </SetupPanel>
      ) : null}

      {exhausted && state.phase !== 'generating' ? (
        <CreditExhaustedNotice source="flashcard" action="flashcards" />
      ) : null}

      {state.phase === 'generating' ? (
        <GeneratingState
          heading="Writing your cards"
          detail="Reading your course material and pulling out the ideas worth drilling."
          elapsed={elapsed}
        />
      ) : null}

      {state.phase === 'error' ? (
        <GenerationError
          failure={state.failure}
          onRetry={() => void handleGenerate()}
          onSeeSources={onClose}
        />
      ) : null}

      {state.phase === 'success' ? (
        <>
          <FlashcardDeck cards={cards} onEscape={onClose} />
          <Provenance context={state.result} />
        </>
      ) : null}
    </Dialog>
  );
}

export default FlashcardModal;
