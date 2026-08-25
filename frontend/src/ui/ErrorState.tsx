import type { ReactNode } from 'react';
import { RefreshCw } from 'lucide-react';
import { Alert } from './Alert';
import { Button } from './Button';

export interface ErrorStateProps {
  title?: string;
  children?: ReactNode;
  onRetry?: () => void;
  retryLabel?: string;
  actions?: ReactNode;
  className?: string;
}

export function ErrorState({
  title,
  children,
  onRetry,
  retryLabel = 'Try again',
  actions,
  className,
}: ErrorStateProps) {
  const recovery =
    onRetry || actions ? (
      <>
        {onRetry ? (
          <Button
            variant="secondary"
            size="sm"
            icon={<RefreshCw aria-hidden="true" />}
            onClick={onRetry}
          >
            {retryLabel}
          </Button>
        ) : null}
        {actions}
      </>
    ) : undefined;

  return (
    <Alert tone="destructive" live="alert" title={title} actions={recovery} className={className}>
      {children}
    </Alert>
  );
}
