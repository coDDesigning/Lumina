import { useCallback, useEffect, useRef, useState } from 'react';
import { Layers, Sparkles } from 'lucide-react';
import { describeGenerationError, isAbortError, isInsufficientCredits } from '@/api/errors';
import type { GenerationFailure } from '@/api/errors';
import { flashcardsAPI } from '@/api/flashcards';
import type { GenerationJobAccepted } from '@/api/types';
import CreditBalance from '@/components/credits/CreditBalance';
import CreditExhaustedNotice from '@/components/credits/CreditExhaustedNotice';
import { useCredits } from '@/context/CreditContext';
import { Button } from '@/ui/Button';
import { Checkbox } from '@/ui/Checkbox';
import { Dialog } from '@/ui/Dialog';
import { FlashcardDeck } from './FlashcardDeck';
import { GenerationError, NoMaterialNotice, SetupPanel } from './GenerationStates';
import { Provenance } from './Provenance';

export interface FlashcardModalProps {
  courseId: number;
  courseName?: string;
  readyDocumentCount: number;
  onClose: () => void;
  onQueued?: (jobId: number) => void;
}

export function FlashcardModal({
  courseId,
  courseName,
  readyDocumentCount,
  onClose,
  onQueued,
}: FlashcardModalProps) {
  const [includeProfileContext, setIncludeProfileContext] = useState(false);
  const [isQueueing, setIsQueueing] = useState(false);
  const [failure, setFailure] = useState<GenerationFailure | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  const { refresh, canAfford, isMetered } = useCredits();
  const exhausted = isMetered && !canAfford('flashcard');
  const hasMaterial = readyDocumentCount > 0;

  const handleQueue = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setFailure(null);
    setIsQueueing(true);
    try {
      const accepted = await flashcardsAPI.enqueue(
        courseId,
        {
          use_profile_knowledge: includeProfileContext,
          include_profile_context: includeProfileContext,
        },
        { signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      await refresh();
      onQueued?.(accepted.job_id);
      onClose();
    } catch (caught) {
      if (controller.signal.aborted || isAbortError(caught)) return;
      const described = describeGenerationError(caught, 'The flashcards could not be queued.');
      if (isInsufficientCredits(described)) await refresh();
      setFailure(described);
    } finally {
      if (!controller.signal.aborted) setIsQueueing(false);
    }
  }, [courseId, includeProfileContext, onClose, onQueued, refresh]);

  const footer = (
    <>
      <Button onClick={onClose}>Cancel</Button>
      <Button
        variant="primary"
        onClick={() => void handleQueue()}
        disabled={!hasMaterial || exhausted || isQueueing}
        isLoading={isQueueing}
        loadingLabel="Queueing flashcards"
        icon={<Sparkles aria-hidden="true" />}
      >
        Make flashcards
      </Button>
    </>
  );

  return (
    <Dialog
      open
      onClose={onClose}
      size="lg"
      title="Flashcards"
      description={courseName}
      mark={<Layers aria-hidden="true" />}
      footer={footer}
      spreadFooter
    >
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

      {exhausted ? (
        <CreditExhaustedNotice source="flashcard" action="flashcards" />
      ) : null}

      {failure ? (
        <GenerationError
          failure={failure}
          onRetry={() => void handleQueue()}
        />
      ) : null}
    </Dialog>
  );
}

export default FlashcardModal;
