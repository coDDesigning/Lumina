import { useMemo, useState } from 'react';
import { describeError } from '@/api/errors';
import { modelsAPI } from '@/api/models';
import { queryKeys } from '@/api/queryKeys';
import { useQuery } from '@/lib/query/useQuery';
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

const LEDGER_LIMIT = 20;

export function AiPreferencesSection() {
  const { user, refreshUser } = useAuth();
  const { status, isMetered } = useCredits();

  const [modelNotice, setModelNotice] = useState<string | null>(null);
  const [modelActionError, setModelActionError] = useState<string | null>(null);

  const modelsQuery = useQuery<AiModelInfo[]>({
    key: queryKeys.models(),
    fetcher: ({ signal }) => modelsAPI.list({ signal }),
    fallbackMessage: "We couldn't load the model list.",
    staleTime: 5 * 60_000,
  });

  const transactionsQuery = useQuery<CreditTransaction[]>({
    key: isMetered && user ? queryKeys.creditTransactions(user.id, LEDGER_LIMIT) : null,
    fetcher: ({ signal }) => userAPI.getCreditTransactions(LEDGER_LIMIT, { signal }),
    fallbackMessage: "We couldn't refresh your credit history.",
  });

  const models = useMemo(() => modelsQuery.data ?? [], [modelsQuery.data]);
  const areModelsLoading = modelsQuery.status === 'pending' || modelsQuery.status === 'idle';
  const modelError = modelActionError ?? modelsQuery.error?.message ?? null;
  const transactions = useMemo(() => transactionsQuery.data ?? [], [transactionsQuery.data]);
  const transactionError = transactionsQuery.error?.message ?? null;

  const selectedId = user?.preferred_model ?? '';
  const selected = models.find((model) => model.id === selectedId) ?? null;


  async function handleModelChange(modelId: string) {
    setModelActionError(null);
    setModelNotice(null);
    try {
      await userAPI.updatePreferredModel(modelId);
      await refreshUser();
      setModelNotice(`Preferred AI model updated to ${modelId}`);
    } catch (caught) {
      setModelActionError(describeError(caught, "That model couldn't be selected.").message);
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

          {transactionError ? <Alert tone="destructive">{transactionError}</Alert> : null}

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
