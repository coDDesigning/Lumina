import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Button } from './Button';
import { Dialog } from './Dialog';
import { Input } from './Input';

export interface ConfirmDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  isPending?: boolean;
  pendingLabel?: string;
  destructive?: boolean;
  confirmPhrase?: string;
  confirmPhraseLabel?: string;
}

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  description,
  children,
  confirmLabel,
  cancelLabel = 'Cancel',
  isPending = false,
  pendingLabel,
  destructive = true,
  confirmPhrase,
  confirmPhraseLabel,
}: ConfirmDialogProps) {
  const [typed, setTyped] = useState('');

  useEffect(() => {
    if (!open) {
      setTyped('');
    }
  }, [open]);

  const phraseSatisfied = !confirmPhrase || typed.trim() === confirmPhrase;
  const canConfirm = phraseSatisfied && !isPending;

  const body =
    children || confirmPhrase ? (
      <>
        {children}
        {confirmPhrase ? (
          <Input
            label={confirmPhraseLabel ?? `Type ${confirmPhrase} to confirm`}
            placeholder={confirmPhrase}
            value={typed}
            autoComplete="off"
            disabled={isPending}
            onChange={(event) => setTyped(event.target.value)}
          />
        ) : null}
      </>
    ) : undefined;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      size="sm"
      mark={destructive ? <AlertTriangle aria-hidden="true" /> : undefined}
      markTone={destructive ? 'destructive' : 'accent'}
      dismissOnScrimClick={!isPending}
      spreadFooter
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={isPending}>
            {cancelLabel}
          </Button>
          <Button
            variant={destructive ? 'destructive' : 'primary'}
            onClick={onConfirm}
            disabled={!canConfirm}
            isLoading={isPending}
            loadingLabel={pendingLabel ?? 'Working'}
          >
            {isPending ? (pendingLabel ?? confirmLabel) : confirmLabel}
          </Button>
        </>
      }
    >
      {body}
    </Dialog>
  );
}
