import { useCallback, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { Plus } from 'lucide-react';
import { describeError } from '@/api/errors';
import { profileKnowledgeAPI } from '@/api/profileKnowledge';
import type { ProfileKnowledgeItem } from '@/api/types';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { Card } from '@/ui/Card';
import { ConfirmDialog } from '@/ui/ConfirmDialog';
import { Dialog } from '@/ui/Dialog';
import { ErrorState } from '@/ui/ErrorState';
import { Input, Textarea } from '@/ui/Input';
import { Skeleton } from '@/ui/Skeleton';
import styles from './AccountPage.module.css';

type Draft = { topic: string; detail: string };

const EMPTY_DRAFT: Draft = { topic: '', detail: '' };

function parseImport(raw: string): { topic: string; detail: string }[] {
  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const split = line.indexOf(':');
      if (split <= 0) {
        return null;
      }
      const topic = line.slice(0, split).trim();
      const detail = line.slice(split + 1).trim();
      return topic && detail ? { topic, detail } : null;
    })
    .filter((entry): entry is { topic: string; detail: string } => entry !== null);
}

export function ProfileKnowledgeSection() {
  const [items, setItems] = useState<ProfileKnowledgeItem[]>([]);
  const [isImporting, setIsImporting] = useState(false);
  const [importText, setImportText] = useState('');
  const [importError, setImportError] = useState<string | null>(null);
  const [isSavingImport, setIsSavingImport] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [editing, setEditing] = useState<ProfileKnowledgeItem | null>(null);
  const [isComposing, setIsComposing] = useState(false);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [deleting, setDeleting] = useState<ProfileKnowledgeItem | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      setItems(await profileKnowledgeAPI.list());
    } catch (caught) {
      setLoadError(describeError(caught, "We couldn't load your background notes.").message);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleImport() {
    const parsed = parseImport(importText);
    if (parsed.length === 0) {
      return;
    }
    setIsSavingImport(true);
    setImportError(null);
    try {
      const created = await profileKnowledgeAPI.importBulk({ items: parsed });
      setItems((previous) => [...created, ...previous]);
      setImportText('');
      setIsImporting(false);
      setNotice(`${created.length} ${created.length === 1 ? 'note' : 'notes'} added.`);
    } catch (caught) {
      setImportError(describeError(caught, 'Those notes could not be saved.').message);
    } finally {
      setIsSavingImport(false);
    }
  }

  function openCompose() {
    setEditing(null);
    setDraft(EMPTY_DRAFT);
    setSaveError(null);
    setNotice(null);
    setIsComposing(true);
  }

  function openEdit(item: ProfileKnowledgeItem) {
    setEditing(item);
    setDraft({ topic: item.topic, detail: item.detail });
    setSaveError(null);
    setNotice(null);
    setIsComposing(true);
  }

  async function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaveError(null);
    setIsSaving(true);

    const payload = { topic: draft.topic.trim(), detail: draft.detail.trim() };

    try {
      if (editing) {
        const updated = await profileKnowledgeAPI.update(editing.id, payload);
        setItems((current) => current.map((item) => (item.id === updated.id ? updated : item)));
        setNotice('Knowledge topic updated.');
      } else {
        const created = await profileKnowledgeAPI.create(payload);
        setItems((current) => [...current, created]);
        setNotice('Knowledge topic added successfully.');
      }
      setIsComposing(false);
      setDraft(EMPTY_DRAFT);
    } catch (caught) {
      setSaveError(describeError(caught, "That note couldn't be saved.").message);
    } finally {
      setIsSaving(false);
    }
  }

  async function handleDelete() {
    if (!deleting) {
      return;
    }
    setDeleteError(null);
    setIsDeleting(true);
    try {
      await profileKnowledgeAPI.delete(deleting.id);
      setItems((current) => current.filter((item) => item.id !== deleting.id));
      setNotice('Knowledge topic removed.');
      setDeleting(null);
    } catch (caught) {
      setDeleteError(describeError(caught, "That note couldn't be removed.").message);
    } finally {
      setIsDeleting(false);
    }
  }

  return (
    <section className={styles.section}>
      <div className={styles.sectionHead}>
        <div>
          <h2 className={styles.sectionHeading}>Your background</h2>
          <p className={styles.sectionLede}>
            Things that stay true across all your courses.
            {items.length > 0
              ? ` ${items.length} ${items.length === 1 ? 'note' : 'notes'}, used only when you ask for it.`
              : ''}
          </p>
        </div>
        <div className={styles.headActions}>
          <Button onClick={() => setIsImporting(true)}>Paste several</Button>
          <Button variant="primary" icon={<Plus aria-hidden="true" />} onClick={openCompose}>
            Add a note
          </Button>
        </div>
      </div>

      {notice ? (
        <Alert tone="success" live="status">
          {notice}
        </Alert>
      ) : null}

      {loadError ? (
        <ErrorState onRetry={() => void load()}>{loadError}</ErrorState>
      ) : null}

      {isLoading ? (
        <div className={styles.stack} aria-hidden="true">
          <Skeleton variant="block" />
          <Skeleton variant="block" />
        </div>
      ) : items.length === 0 && !loadError ? (
        <Card elevation="flat" padding="lg">
          <p className={styles.rowBody}>
            Nothing here yet. Notes like which university you attend, how your exams are usually
            structured, or how you prefer to study all make generated material fit you better.
          </p>
        </Card>
      ) : (
        <div className={styles.stack}>
          {items.map((item) => (
            <Card key={item.id} className={styles.row}>
              <div className={styles.rowText}>
                <p className={styles.rowTitle}>{item.topic}</p>
                <p className={styles.rowBody}>{item.detail}</p>
              </div>
              <div className={styles.rowActions}>
                <Button variant="ghost" size="sm" onClick={() => openEdit(item)}>
                  Edit
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Delete ${item.topic}`}
                  onClick={() => {
                    setDeleteError(null);
                    setNotice(null);
                    setDeleting(item);
                  }}
                >
                  Delete
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Dialog
        open={isComposing}
        onClose={() => setIsComposing(false)}
        title={editing ? 'Edit knowledge topic' : 'Add knowledge topic'}
        description="Written notes only. There is no document upload here — profile-level ingestion is not built."
        size="md"
        spreadFooter
        footer={
          <>
            <Button variant="ghost" onClick={() => setIsComposing(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              form="knowledge-form"
              variant="primary"
              isLoading={isSaving}
              loadingLabel="Saving"
            >
              Save Topic
            </Button>
          </>
        }
      >
        <form id="knowledge-form" className={styles.formGrid} onSubmit={handleSave}>
          {saveError ? (
            <Alert tone="destructive" live="alert">
              {saveError}
            </Alert>
          ) : null}
          <Input
            label="Topic Name"
            required
            autoFocus
            value={draft.topic}
            onChange={(event) => setDraft((current) => ({ ...current, topic: event.target.value }))}
            placeholder="e.g. How my exams are structured"
          />
          <Textarea
            label="Knowledge Details & Background"
            required
            rows={5}
            value={draft.detail}
            onChange={(event) => setDraft((current) => ({ ...current, detail: event.target.value }))}
            placeholder="Two-hour written papers, roughly half derivations and half explanation questions."
          />
        </form>
      </Dialog>

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => {
          setDeleting(null);
          setDeleteError(null);
        }}
        onConfirm={handleDelete}
        title={deleting ? `Remove “${deleting.topic}”?` : 'Remove note?'}
        description="Lumina will stop using this note as background. Your courses are not affected."
        confirmLabel="Remove"
        pendingLabel="Removing"
        isPending={isDeleting}
      >
        {deleteError ? (
          <Alert tone="destructive" live="alert">
            {deleteError}
          </Alert>
        ) : null}
      </ConfirmDialog>

      <Dialog
        open={isImporting}
        onClose={() => {
          setIsImporting(false);
          setImportError(null);
        }}
        title="Paste several notes"
        description="One note a line, as Topic: detail."
        footer={
          <>
            <Button onClick={() => setIsImporting(false)}>Cancel</Button>
            <Button
              variant="primary"
              onClick={() => void handleImport()}
              isLoading={isSavingImport}
              loadingLabel="Saving your notes"
              disabled={parseImport(importText).length === 0}
            >
              Save {parseImport(importText).length || ''} notes
            </Button>
          </>
        }
        spreadFooter
      >
        {importError ? (
          <Alert tone="destructive" live="alert">
            {importError}
          </Alert>
        ) : null}
        <Textarea
          label="Your notes"
          hint="For example — How exams usually look: two-hour written papers, mostly derivations."
          rows={9}
          value={importText}
          onChange={(event) => setImportText(event.target.value)}
        />
      </Dialog>

      <p className={styles.footnote}>
        These notes belong to you, not to any course. Deleting a course leaves them untouched, and
        your course material always wins when the two disagree.
      </p>
    </section>
  );
}
