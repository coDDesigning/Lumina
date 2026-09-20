import { useState } from 'react';
import { describeGenerationError } from '@/api/errors';
import { modelsAPI } from '@/api/models';
import { queryKeys } from '@/api/queryKeys';
import { useQuery } from '@/lib/query/useQuery';
import type { AiModelInfo, ModelTestResult } from '@/api/types';
import { Badge, type BadgeTone } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { ErrorState } from '@/ui/ErrorState';
import { Skeleton } from '@/ui/Skeleton';
import styles from './ModelChecksSection.module.css';

type RowStatus = 'waiting' | 'testing' | 'passed' | 'failed';

interface RowState {
  status: RowStatus;
  result: ModelTestResult | null;
  httpError: string | null;
}

function waitingRow(): RowState {
  return { status: 'waiting', result: null, httpError: null };
}

const STATUS_BADGE: Record<RowStatus, { tone: BadgeTone; label: string }> = {
  waiting: { tone: 'neutral', label: 'Waiting' },
  testing: { tone: 'processing', label: 'Testing…' },
  passed: { tone: 'success', label: 'Passed' },
  failed: { tone: 'destructive', label: 'Failed' },
};

function formatLatency(latencyMs: number): string {
  return `${(latencyMs / 1000).toFixed(1)} s`;
}

export function ModelChecksSection() {
  const modelsQuery = useQuery<AiModelInfo[]>({
    key: queryKeys.models(),
    fetcher: ({ signal }) => modelsAPI.list({ signal }),
    fallbackMessage: "We couldn't load the model list.",
  });

  const [rows, setRows] = useState<Record<string, RowState>>({});
  const [isRunning, setIsRunning] = useState(false);

  const models = modelsQuery.data ?? [];
  const isLoading = modelsQuery.status === 'pending' || modelsQuery.status === 'idle';
  const loadError = modelsQuery.error?.message ?? null;

  async function runAll() {
    setIsRunning(true);
    const reset: Record<string, RowState> = {};
    models.forEach((model) => {
      reset[model.id] = waitingRow();
    });
    setRows(reset);

    for (const model of models) {
      setRows((current) => ({
        ...current,
        [model.id]: { status: 'testing', result: null, httpError: null },
      }));
      try {
        const outcome = await modelsAPI.test(model.id);
        setRows((current) => ({
          ...current,
          [model.id]: { status: outcome.ok ? 'passed' : 'failed', result: outcome, httpError: null },
        }));
      } catch (caught) {
        setRows((current) => ({
          ...current,
          [model.id]: {
            status: 'failed',
            result: null,
            httpError: describeGenerationError(caught, "That model couldn't be tested.").message,
          },
        }));
      }
    }

    setIsRunning(false);
  }

  return (
    <section className={styles.section} aria-labelledby="model-checks-title">
      <div className={styles.heading}>
        <div>
          <h2 id="model-checks-title" className={styles.sectionTitle}>
            Test all models
          </h2>
          <p className={styles.subtitle}>
            Checks whether each configured model is available, one at a time.
          </p>
        </div>
        <Button
          size="sm"
          onClick={() => void runAll()}
          isLoading={isRunning}
          disabled={models.length === 0 || isLoading}
        >
          {isRunning ? 'Testing…' : 'Test all models'}
        </Button>
      </div>

      {isLoading ? (
        <Skeleton variant="block" />
      ) : loadError ? (
        <ErrorState onRetry={() => void modelsQuery.refetch()}>{loadError}</ErrorState>
      ) : models.length === 0 ? (
        <p className={styles.subtitle}>No models are configured on this deployment.</p>
      ) : (
        <ul className={styles.list}>
          {models.map((model) => {
            const row = rows[model.id] ?? waitingRow();
            const badge = STATUS_BADGE[row.status];
            const message = row.result && !row.result.ok ? row.result.message : row.httpError;

            return (
              <li key={model.id} className={styles.row}>
                <div className={styles.rowText}>
                  <p className={styles.rowTitle}>{model.display_name}</p>
                  {row.result?.latency_ms != null ? (
                    <p className={styles.rowMeta}>{formatLatency(row.result.latency_ms)}</p>
                  ) : null}
                  {row.result?.supports_vision === false ? (
                    <p className={styles.rowMeta}>
                      This model can't read images, so figures won't be described
                    </p>
                  ) : null}
                  {message ? <p className={styles.rowMeta}>{message}</p> : null}
                  {row.result?.base_url ? (
                    <p className={styles.rowMeta}>
                      Ollama base URL: <code>{row.result.base_url}</code>
                    </p>
                  ) : null}
                  {row.result?.base_url_fallback ? (
                    <p className={styles.rowWarning}>
                      Using the fallback address because the configured OLLAMA_BASE_URL did not
                      resolve.
                    </p>
                  ) : null}
                </div>
                <Badge tone={badge.tone}>{badge.label}</Badge>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
