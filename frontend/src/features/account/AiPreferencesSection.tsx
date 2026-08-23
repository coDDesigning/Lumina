import { useEffect, useState } from 'react';
import { describeError } from '@/api/errors';
import { modelsAPI } from '@/api/models';
import { userAPI } from '@/api/user';
import type { AiModelInfo, CreditTransaction } from '@/api/types';
import { formatDelta, transactionLabel } from '@/api/creditLabels';
import { useAuth } from '@/context/AuthContext';
import { useCredits } from '@/context/CreditContext';
import { Alert } from '@/ui/Alert';
import { Badge } from '@/ui/Badge';
import { Card } from '@/ui/Card';
import { Select } from '@/ui/Input';
import { Skeleton } from '@/ui/Skeleton';
import styles from './AccountPage.module.css';

const COST_ROWS: { source: string; label: string }[] = [
  { source: 'study_guide', label: 'Study guide' },
  { source: 'quiz', label: 'Quiz' },
  { source: 'quiz_open_ended', label: 'Quiz including written questions' },
  { source: 'flashcard', label: 'Flashcards' },
  { source: 'course_qa', label: 'A question' },
  { source: 'ai_tutor', label: 'A tutoring turn' },
  { source: 'prompt_generator', label: 'A written prompt' },
];

function formatDate(value: string | null): string | null {
  if (!value) {
    return null;
  }
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) {
    return null;
  }
  return new Intl.DateTimeFormat('en', { day: 'numeric', month: 'long' }).format(parsed);
}

export function AiPreferencesSection() {
  const { user, refreshUser } = useAuth();
  const { status, isMetered } = useCredits();

  const [models, setModels] = useState<AiModelInfo[]>([]);
  const [areModelsLoading, setAreModelsLoading] = useState(true);
  const [modelError, setModelError] = useState<string | null>(null);
  const [modelNotice, setModelNotice] = useState<string | null>(null);
  const [transactions, setTransactions] = useState<CreditTransaction[]>([]);

  const selectedId = user?.preferred_model ?? '';
  const selected = models.find((model) => model.id === selectedId) ?? null;

  useEffect(() => {
    let cancelled = false;

    modelsAPI
      .list()
      .then((result) => {
        if (cancelled) return;
        setModels(result);
        setAreModelsLoading(false);
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setAreModelsLoading(false);
        setModelError(describeError(caught, "We couldn't load the model list.").message);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!isMetered) {
      setTransactions([]);
      return;
    }

    let cancelled = false;
    userAPI
      .getCreditTransactions(20)
      .then((result) => {
        if (!cancelled) setTransactions(result);
      })
      .catch(() => {
      });

    return () => {
      cancelled = true;
    };
  }, [isMetered]);

  async function handleModelChange(modelId: string) {
    setModelError(null);
    setModelNotice(null);
    try {
      await userAPI.updatePreferredModel(modelId);
      await refreshUser();
      setModelNotice(`Preferred AI model updated to ${modelId}`);
    } catch (caught) {
      setModelError(describeError(caught, "That model couldn't be selected.").message);
    }
  }

  const nextGrant = formatDate(status?.next_grant_at ?? null);

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionHeading}>AI{isMetered ? ' & credits' : ''}</h2>
      <p className={styles.sectionLede}>
        Which model writes your study guides, quizzes, flashcards and answers.
      </p>

      {modelError ? (
        <Alert tone="destructive" live="alert">
          {modelError}
        </Alert>
      ) : null}
      {modelNotice ? (
        <Alert tone="success" live="status">
          {modelNotice}
        </Alert>
      ) : null}

      {areModelsLoading ? (
        <Skeleton variant="block" />
      ) : (
        <Select
          label="Preferred AI Model"
          value={selectedId}
          onChange={(event) => void handleModelChange(event.target.value)}
        >
          {models.map((model) => (
            <option key={model.id} value={model.id}>
              {model.display_name}
              {model.is_default ? ' (default)' : ''}
            </option>
          ))}
        </Select>
      )}

      {selected ? (
        <Card padding="md" className={styles.section} data-testid="model-details-card">
          <p className={styles.rowTitle}>{selected.display_name}</p>
          {selected.description ? <p className={styles.rowBody}>{selected.description}</p> : null}
          <div className={styles.capabilities}>
            {selected.cost_hint ? <Badge tone="accent">{selected.cost_hint}</Badge> : null}
            {selected.is_local ? <Badge tone="success">Runs on this machine</Badge> : null}
            {(selected.capabilities ?? []).map((capability) => (
              <Badge key={capability} data-testid={`model-capability-${capability}`}>
                {capability.replace(/_/g, ' ')}
              </Badge>
            ))}
          </div>
        </Card>
      ) : null}

      {isMetered && status ? (
        <>
          <Card padding="lg" className={styles.section}>
            <div className={styles.balance}>
              <div>
                <span className={styles.sectionLabel}>Credits left</span>
                <p className={styles.balanceValue}>{status.credits}</p>
              </div>
              <div className={styles.balanceMeta}>
                {status.monthly_grant != null && nextGrant ? (
                  <p>
                    {status.monthly_grant} more on {nextGrant}
                  </p>
                ) : null}
                {status.balance_cap != null ? <p>Balance is capped at {status.balance_cap}</p> : null}
              </div>
            </div>

            <div className={styles.costs}>
              <span className={styles.sectionLabel}>What things cost</span>
              {COST_ROWS.filter((row) => status.generation_costs?.[row.source] != null).map(
                (row) => (
                  <p key={row.source} className={styles.costRow}>
                    <span>{row.label}</span>
                    <strong>{status.generation_costs?.[row.source]}</strong>
                  </p>
                ),
              )}
              <p className={styles.rowBody}>
                There is nothing to buy. If you run out, you can wait for the monthly credits, ask
                an administrator, or self-host — which is not metered at all.
              </p>
            </div>
          </Card>

          {transactions.length > 0 ? (
            <Card padding="none" className={styles.section}>
              <table className={styles.ledger}>
                <tbody>
                  {transactions.map((entry) => (
                    <tr key={entry.id}>
                      <td>{transactionLabel(entry)}</td>
                      <td
                        className={`${styles.delta} ${entry.delta >= 0 ? styles.positive : styles.negative}`}
                      >
                        {formatDelta(entry.delta)}
                      </td>
                      <td className={styles.delta}>{entry.balance_after}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          ) : null}
        </>
      ) : (
        <Card padding="lg" className={styles.section} elevation="flat">
          <p className={styles.rowTitle}>This account is not metered</p>
          <p className={styles.rowBody}>
            There is no usage limit and nothing to track. Generate as much as your setup will take.
          </p>
        </Card>
      )}
    </section>
  );
}
