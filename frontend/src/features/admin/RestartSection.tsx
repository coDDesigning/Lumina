import { useEffect, useRef, useState } from 'react';
import {
  describeSettingsError,
  systemSettingsAPI,
  type RestartState,
  type SystemSettingsInventory,
} from '@/api/systemSettings';
import { Alert } from '@/ui/Alert';
import { Badge, type BadgeTone } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { ConfirmDialog } from '@/ui/ConfirmDialog';
import { useToast } from '@/ui/toastContext';
import styles from './RestartSection.module.css';

const POLL_START_MS = 2000;
const POLL_MAX_MS = 10000;
const POLL_LIMIT_MS = 300000;

const STATE_LABELS: Record<RestartState, string> = {
  queued: 'Queued',
  draining: 'Waiting for running work',
  restarting: 'Restarting',
  ready: 'Ready',
  failed: 'Failed',
  rolled_back: 'Rolled back',
};

const STATE_TONES: Record<RestartState, BadgeTone> = {
  queued: 'processing',
  draining: 'processing',
  restarting: 'processing',
  ready: 'success',
  failed: 'destructive',
  rolled_back: 'warning',
};

export interface RestartSectionProps {
  inventory: SystemSettingsInventory;
  hasUnsavedChanges: boolean;
  onSettled: (inventory: SystemSettingsInventory) => void;
}

export function RestartSection({
  inventory,
  hasUnsavedChanges,
  onSettled,
}: RestartSectionProps) {
  const { showToast } = useToast();
  const [confirming, setConfirming] = useState(false);
  const [isRestarting, setIsRestarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [waiting, setWaiting] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current);
    },
    [],
  );

  const restart = inventory.restart;
  const pendingCount = inventory.pending_keys.length;
  const canRestart = inventory.pending_restart && !hasUnsavedChanges;

  function poll(requestId: string, delay: number, elapsed: number) {
    timer.current = setTimeout(() => {
      void systemSettingsAPI
        .restartStatus(requestId)
        .then((next) => {
          onSettled(next);
          const state = next.restart?.state;
          if (state === 'ready' && next.active_revision === next.saved_revision) {
            setWaiting(false);
            showToast({
              tone: 'success',
              title: 'Lumina restarted',
              message: 'The saved configuration is now active.',
            });
            return;
          }
          if (state === 'failed' || state === 'rolled_back') {
            setWaiting(false);
            setError(
              next.restart?.detail ??
                'The restart did not complete. The previous configuration is active.',
            );
            return;
          }
          continuePolling(requestId, delay, elapsed);
        })
        .catch(() => {
          continuePolling(requestId, delay, elapsed);
        });
    }, delay);
  }

  function continuePolling(requestId: string, delay: number, elapsed: number) {
    const next = elapsed + delay;
    if (next >= POLL_LIMIT_MS) {
      setWaiting(false);
      setError(
        'Lumina did not report itself ready in time. Check the container, then reload this page.',
      );
      return;
    }
    poll(requestId, Math.min(delay * 1.5, POLL_MAX_MS), next);
  }

  async function handleRestart() {
    setConfirming(false);
    setIsRestarting(true);
    setError(null);
    try {
      const accepted = await systemSettingsAPI.restart(inventory.saved_revision);
      showToast({
        tone: 'info',
        title: 'Restart requested',
        message: 'Lumina will disconnect briefly while it applies the new settings.',
      });
      setWaiting(true);
      poll(accepted.request_id, POLL_START_MS, 0);
    } catch (caught) {
      setError(describeSettingsError(caught, 'Could not request a restart.').message);
    } finally {
      setIsRestarting(false);
    }
  }

  return (
    <section className={styles.section} aria-labelledby="restart-heading">
      <h2 id="restart-heading" className={styles.heading}>
        Restart Lumina
      </h2>
      <p className={styles.lede}>
        Saved settings are read when Lumina starts, so they apply on the next restart.
      </p>

      <dl className={styles.facts}>
        <div className={styles.fact}>
          <dt>Active revision</dt>
          <dd className="tabular">{inventory.active_revision}</dd>
        </div>
        <div className={styles.fact}>
          <dt>Saved revision</dt>
          <dd className="tabular">{inventory.saved_revision}</dd>
        </div>
        <div className={styles.fact}>
          <dt>Pending changes</dt>
          <dd className="tabular">{pendingCount}</dd>
        </div>
        <div className={styles.fact}>
          <dt>Last restart</dt>
          <dd>
            {restart ? (
              <Badge tone={STATE_TONES[restart.state]}>
                {STATE_LABELS[restart.state]}
              </Badge>
            ) : (
              'No restart on record'
            )}
          </dd>
        </div>
      </dl>

      {restart?.detail ? <p className={styles.detail}>{restart.detail}</p> : null}

      {inventory.rolled_back_from !== null ? (
        <Alert tone="warning" title="A previous restart was rolled back" live="status">
          Revision {inventory.rolled_back_from} could not start, so Lumina returned to
          the last configuration that did. Review the values before trying again.
        </Alert>
      ) : null}

      {error ? (
        <Alert tone="destructive" title="Restart problem" live="alert">
          {error}
        </Alert>
      ) : null}

      {waiting ? (
        <Alert tone="info" title="Waiting for Lumina to come back" live="status">
          This page keeps checking until the new configuration is active.
        </Alert>
      ) : null}

      {!inventory.supervised_restart ? (
        <p className={styles.unavailable}>
          Nothing is configured to restart Lumina automatically, so it will not stop
          itself. Apply saved settings by restarting the container yourself.
        </p>
      ) : hasUnsavedChanges ? (
        <p className={styles.unavailable}>
          Save or discard your edits before restarting.
        </p>
      ) : !inventory.pending_restart ? (
        <p className={styles.unavailable}>
          The saved configuration is already active, so there is nothing to apply.
        </p>
      ) : (
        <div className={styles.actions}>
          <Button
            variant="destructive"
            isLoading={isRestarting || waiting}
            loadingLabel="Restarting"
            onClick={() => setConfirming(true)}
          >
            Restart Lumina
          </Button>
        </div>
      )}

      <ConfirmDialog
        open={confirming && canRestart}
        onClose={() => setConfirming(false)}
        onConfirm={() => void handleRestart()}
        title="Restart Lumina?"
        confirmLabel="Restart now"
        confirmPhrase="RESTART"
        confirmPhraseLabel="Type RESTART to confirm"
        isPending={isRestarting}
        pendingLabel="Requesting"
        description={
          <>
            Lumina will stop and start again to apply {pendingCount} saved change
            {pendingCount === 1 ? '' : 's'}. This interface will disconnect for a
            moment and reconnect on its own.
          </>
        }
      >
        <p className={styles.dialogBody}>
          Document and generation work still running is allowed to finish first. Any
          that is still going when the wait expires returns to the queue and runs
          again, so nothing is lost.
        </p>
        {inventory.pending_keys.length > 0 ? (
          <ul className={styles.keyList}>
            {inventory.pending_keys.map((key) => (
              <li key={key}>
                <code>{key}</code>
              </li>
            ))}
          </ul>
        ) : null}
      </ConfirmDialog>
    </section>
  );
}
