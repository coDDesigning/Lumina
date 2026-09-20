import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { queryKeys } from '@/api/queryKeys';
import {
  describeSettingsError,
  systemSettingsAPI,
  type SystemSettingRow,
  type SystemSettingsInventory,
} from '@/api/systemSettings';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { queryCache } from '@/lib/query/cache';
import { useQuery } from '@/lib/query/useQuery';
import { Alert } from '@/ui/Alert';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { ConfirmDialog } from '@/ui/ConfirmDialog';
import { EmptyState } from '@/ui/EmptyState';
import { ErrorState } from '@/ui/ErrorState';
import { Input, Select } from '@/ui/Input';
import { PageHeader } from '@/ui/PageHeader';
import { Skeleton } from '@/ui/Skeleton';
import { useToast } from '@/ui/toastContext';
import { RestartSection } from './RestartSection';
import { SettingRow } from './SettingRow';
import styles from './SystemSettingsPage.module.css';

type Drafts = Record<string, string>;

function matches(setting: SystemSettingRow, term: string): boolean {
  if (!term) return true;
  const needle = term.toLowerCase();
  return (
    setting.key.toLowerCase().includes(needle) ||
    setting.label.toLowerCase().includes(needle) ||
    setting.help.toLowerCase().includes(needle)
  );
}

export default function SystemSettingsPage() {
  useDocumentTitle('System settings');
  const { showToast } = useToast();
  const [params, setParams] = useSearchParams();
  const [drafts, setDrafts] = useState<Drafts>({});
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [actionError, setActionError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [confirmingReset, setConfirmingReset] = useState(false);
  const [confirmingSave, setConfirmingSave] = useState(false);

  const search = params.get('q') ?? '';
  const section = params.get('section') ?? '';

  const query = useQuery<SystemSettingsInventory>({
    key: queryKeys.adminSystemSettings(),
    fetcher: ({ signal }) => systemSettingsAPI.get({ signal }),
    fallbackMessage: 'Could not load system settings.',
  });

  const inventory = query.data;
  const isLoading = query.status === 'pending' || query.status === 'idle';

  const visible = useMemo(() => {
    if (!inventory) return [];
    return inventory.settings.filter(
      (setting) =>
        matches(setting, search) && (section === '' || setting.section === section),
    );
  }, [inventory, search, section]);

  const grouped = useMemo(() => {
    if (!inventory) return [];
    return inventory.sections
      .map((name) => ({
        name,
        rows: visible.filter((setting) => setting.section === name),
      }))
      .filter((group) => group.rows.length > 0);
  }, [inventory, visible]);

  const dirtyKeys = Object.keys(drafts);
  const needsConfirmation = useMemo(() => {
    if (!inventory) return false;
    return inventory.settings.some(
      (setting) => setting.requires_confirmation && drafts[setting.key] !== undefined,
    );
  }, [inventory, drafts]);

  function setFilter(name: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) {
      next.set(name, value);
    } else {
      next.delete(name);
    }
    setParams(next, { replace: true });
  }

  function handleChange(key: string, value: string) {
    setDrafts((previous) => ({ ...previous, [key]: value }));
    setFieldErrors((previous) => {
      if (previous[key] === undefined) return previous;
      const next = { ...previous };
      delete next[key];
      return next;
    });
  }

  function applyInventory(next: SystemSettingsInventory) {
    queryCache.setData(queryKeys.adminSystemSettings(), next);
  }

  function handleFailure(caught: unknown, fallback: string) {
    const described = describeSettingsError(caught, fallback);
    const perField: Record<string, string> = {};
    for (const item of described.fieldErrors) {
      if (item.key) perField[item.key] = item.message;
    }
    setFieldErrors(perField);
    const keyed = Object.keys(perField).length;
    setActionError(
      keyed > 0
        ? `Nothing was saved. ${keyed} setting${keyed === 1 ? '' : 's'} below need${keyed === 1 ? 's' : ''} attention.`
        : described.message,
    );
    if (described.code === 'settings_revision_conflict') {
      void query.refetch();
    }
  }

  async function save() {
    if (!inventory || dirtyKeys.length === 0) return;
    setConfirmingSave(false);
    setIsSaving(true);
    setActionError(null);
    setFieldErrors({});
    try {
      const next = await systemSettingsAPI.update({
        expected_revision: inventory.saved_revision,
        values: drafts,
      });
      applyInventory(next);
      setDrafts({});
      showToast({
        tone: 'success',
        title: 'Settings saved',
        message: 'Restart Lumina to apply them.',
      });
    } catch (caught) {
      handleFailure(caught, 'Could not save these settings.');
    } finally {
      setIsSaving(false);
    }
  }

  async function resetOne(setting: SystemSettingRow) {
    if (!inventory) return;
    setActionError(null);
    try {
      const next = await systemSettingsAPI.resetKey(
        setting.key,
        inventory.saved_revision,
      );
      applyInventory(next);
      setDrafts((previous) => {
        const draft = { ...previous };
        delete draft[setting.key];
        return draft;
      });
      showToast({
        tone: 'success',
        title: `${setting.key} reset`,
        message: 'Restart Lumina to apply it.',
      });
    } catch (caught) {
      handleFailure(caught, `Could not reset ${setting.key}.`);
    }
  }

  async function resetAll() {
    if (!inventory) return;
    setConfirmingReset(false);
    setActionError(null);
    try {
      const next = await systemSettingsAPI.resetAll(inventory.saved_revision);
      applyInventory(next);
      setDrafts({});
      showToast({
        tone: 'success',
        title: 'Overrides reset',
        message: 'Restart Lumina to apply the deployment values.',
      });
    } catch (caught) {
      handleFailure(caught, 'Could not reset the overrides.');
    }
  }

  return (
    <div className={styles.page}>
      <PageHeader
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'System settings' }]}
        badges={<Badge tone="accent">Administrator</Badge>}
      />
      <div className={styles.body}>
        <h1 className={styles.title}>System settings</h1>
        <p className={styles.subtitle}>
          Every configuration key this installation supports. An override set here
          outranks the value in .env and survives a restart. Your .env file is never
          modified.
        </p>

        {isLoading ? <Skeleton variant="block" height="24rem" /> : null}

        {query.status === 'error' ? (
          <ErrorState onRetry={() => void query.refetch()}>
            {query.error?.message}
          </ErrorState>
        ) : null}

        {inventory ? (
          <>
            {inventory.pending_restart ? (
              <Alert tone="warning" title="Changes are waiting for a restart" live="status">
                {inventory.pending_keys.length} saved change
                {inventory.pending_keys.length === 1 ? '' : 's'} will apply the next
                time Lumina starts.
              </Alert>
            ) : null}

            {actionError ? (
              <Alert tone="destructive" title="That did not work" live="alert">
                {actionError}
              </Alert>
            ) : null}

            <div className={styles.filters}>
              <Input
                label="Search settings"
                hideLabel
                type="search"
                placeholder="Search by name, key or description"
                fieldClassName={styles.search}
                value={search}
                onChange={(event) => setFilter('q', event.target.value)}
              />
              <Select
                label="Section"
                hideLabel
                fieldClassName={styles.sectionFilter}
                value={section}
                onChange={(event) => setFilter('section', event.target.value)}
              >
                <option value="">All sections</option>
                {inventory.sections.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </Select>
            </div>

            <div className={styles.toolbar}>
              <p className={styles.count}>
                Showing {visible.length} of {inventory.settings.length} settings
              </p>
              <div className={styles.toolbarActions}>
                {inventory.override_count > 0 ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setConfirmingReset(true)}
                  >
                    Reset all overrides
                  </Button>
                ) : null}
                {dirtyKeys.length > 0 ? (
                  <>
                    <Button variant="ghost" size="sm" onClick={() => setDrafts({})}>
                      Discard edits
                    </Button>
                    <Button
                      variant="primary"
                      size="sm"
                      isLoading={isSaving}
                      loadingLabel="Saving"
                      onClick={() =>
                        needsConfirmation ? setConfirmingSave(true) : void save()
                      }
                    >
                      {`Save ${dirtyKeys.length} change${dirtyKeys.length === 1 ? '' : 's'}`}
                    </Button>
                  </>
                ) : null}
              </div>
            </div>

            {grouped.length === 0 ? (
              <EmptyState
                title="No settings match"
                description="Try a different search term or section."
              />
            ) : (
              grouped.map((group) => (
                <section
                  key={group.name}
                  className={styles.section}
                  aria-label={group.name}
                >
                  <h2 className={styles.sectionHeading}>{group.name}</h2>
                  <div className={styles.rows}>
                    {group.rows.map((setting) => (
                      <SettingRow
                        key={setting.key}
                        setting={setting}
                        draft={drafts[setting.key]}
                        error={fieldErrors[setting.key]}
                        disabled={isSaving}
                        onChange={handleChange}
                        onReset={(target) => void resetOne(target)}
                      />
                    ))}
                  </div>
                </section>
              ))
            )}

            <RestartSection
              inventory={inventory}
              hasUnsavedChanges={dirtyKeys.length > 0}
              onSettled={applyInventory}
            />

            <ConfirmDialog
              open={confirmingReset}
              onClose={() => setConfirmingReset(false)}
              onConfirm={() => void resetAll()}
              title="Reset every override?"
              confirmLabel="Reset overrides"
              description="Each setting falls back to the value in .env, or to its documented default where .env does not set one. Your .env file is not touched."
            />

            <ConfirmDialog
              open={confirmingSave}
              onClose={() => setConfirmingSave(false)}
              onConfirm={() => void save()}
              title="Save high-risk changes?"
              confirmLabel="Save changes"
              confirmPhrase="SAVE"
              confirmPhraseLabel="Type SAVE to confirm"
              isPending={isSaving}
              pendingLabel="Saving"
              description="One or more of these settings controls identity or encryption. Changing them does not migrate existing data, which may become unreadable under the new configuration."
            />
          </>
        ) : null}
      </div>
    </div>
  );
}
