import { useEffect, useRef, useState } from 'react';
import { describeGenerationError } from '@/api/errors';
import { modelsAPI } from '@/api/models';
import type { ModelTestResult } from '@/api/types';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { ErrorState } from '@/ui/ErrorState';
import styles from './ModelTestControl.module.css';

export interface ModelTestControlProps {
  modelId: string | null;
  isAdmin: boolean;
}

function formatLatency(latencyMs: number): string {
  return `${(latencyMs / 1000).toFixed(1)} s`;
}

export function ModelTestControl({ modelId, isAdmin }: ModelTestControlProps) {
  const [isPending, setIsPending] = useState(false);
  const [result, setResult] = useState<ModelTestResult | null>(null);
  const [httpError, setHttpError] = useState<string | null>(null);
  const requestToken = useRef(0);

  useEffect(() => {
    setResult(null);
    setHttpError(null);
    setIsPending(false);
    return () => {
      requestToken.current += 1;
    };
  }, [modelId]);

  async function runTest() {
    const token = ++requestToken.current;
    setIsPending(true);
    setHttpError(null);
    try {
      const outcome = await modelsAPI.test(modelId);
      if (requestToken.current !== token) {
        return;
      }
      setResult(outcome);
    } catch (caught) {
      if (requestToken.current !== token) {
        return;
      }
      setResult(null);
      setHttpError(describeGenerationError(caught, "That model couldn't be tested.").message);
    } finally {
      if (requestToken.current === token) {
        setIsPending(false);
      }
    }
  }

  const showAdminAddress = isAdmin && result?.base_url != null;

  return (
    <div className={styles.control}>
      <Button
        variant="secondary"
        size="sm"
        isLoading={isPending}
        onClick={() => void runTest()}
      >
        {isPending ? 'Testing…' : 'Test model'}
      </Button>

      {result?.ok ? (
        <Alert tone="success" live="status" className={styles.outcome}>
          {result.latency_ms != null ? (
            <p>This model is available on the provider (checked in {formatLatency(result.latency_ms)})</p>
          ) : null}
          {result.supports_vision === false ? (
            <p>This model can't read images, so figures won't be described</p>
          ) : null}
        </Alert>
      ) : null}

      {result && !result.ok ? (
        <ErrorState
          className={styles.outcome}
          onRetry={() => void runTest()}
          isRetrying={isPending}
        >
          {result.message}
        </ErrorState>
      ) : null}

      {httpError ? (
        <ErrorState
          className={styles.outcome}
          onRetry={() => void runTest()}
          isRetrying={isPending}
        >
          {httpError}
        </ErrorState>
      ) : null}

      {showAdminAddress ? (
        <div className={styles.adminDetail}>
          <p className={styles.detail}>
            Ollama base URL: <code>{result?.base_url}</code>
          </p>
          {result?.base_url_fallback ? (
            <p className={styles.warning}>
              Using the fallback address because the configured OLLAMA_BASE_URL did not resolve.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
