import { useState } from 'react';
import type { FormEvent } from 'react';
import { Wand2 } from 'lucide-react';
import { promptGeneratorAPI } from '@/api/promptGenerator';
import { describeGenerationError, isInsufficientCredits } from '@/api/errors';
import CreditExhaustedNotice from '@/components/credits/CreditExhaustedNotice';
import { useCredits } from '@/context/CreditContext';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { Dialog } from '@/ui/Dialog';
import { Input } from '@/ui/Input';

export interface PromptGeneratorDialogProps {
  open: boolean;
  onClose: () => void;
  /** Receives the generated prompt so it can be dropped into the composer. */
  onGenerated: (prompt: string) => void;
}

/**
 * Turns a rough description into a fuller prompt. It is a real, credited
 * backend feature, so it keeps its own exhaustion notice and error handling.
 */
export function PromptGeneratorDialog({ open, onClose, onGenerated }: PromptGeneratorDialogProps) {
  const { refresh: refreshCredits, canAfford, isMetered } = useCredits();
  const [description, setDescription] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const exhausted = isMetered && !canAfford('prompt_generator');

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const request = description.trim();
    if (!request || isGenerating || exhausted) {
      return;
    }

    setIsGenerating(true);
    setError(null);

    try {
      const result = await promptGeneratorAPI.generate({ description: request });
      onGenerated(result.generated_prompt);
      setDescription('');
      void refreshCredits();
      onClose();
    } catch (caught) {
      const described = describeGenerationError(
        caught,
        "That prompt couldn't be written. Try again.",
      );
      if (isInsufficientCredits(described)) {
        await refreshCredits();
        setError(null);
      } else {
        setError(described.message);
      }
    } finally {
      setIsGenerating(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Help me word this"
      description="Describe roughly what you want and Lumina will write a fuller prompt into the box for you to edit."
      size="sm"
      mark={<Wand2 aria-hidden="true" />}
      spreadFooter
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            form="prompt-generator-form"
            variant="primary"
            isLoading={isGenerating}
            loadingLabel="Writing"
            disabled={exhausted}
          >
            Write the prompt
          </Button>
        </>
      }
    >
      <form id="prompt-generator-form" onSubmit={handleSubmit}>
        {error ? (
          <Alert tone="destructive" live="alert">
            {error}
          </Alert>
        ) : null}

        <Input
          label="Prompt description"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          placeholder="e.g. Create an exam revision guide"
          disabled={isGenerating}
          autoFocus
        />

        {exhausted ? <CreditExhaustedNotice source="prompt_generator" action="a prompt" /> : null}
      </form>
    </Dialog>
  );
}
