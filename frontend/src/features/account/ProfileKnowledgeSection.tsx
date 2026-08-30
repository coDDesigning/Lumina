import { useCallback, useMemo, useRef, useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';
import { AlertTriangle, Check, Loader2, Plus, RotateCcw, Trash2, Upload } from 'lucide-react';
import { describeError } from '@/api/errors';
import { profileKnowledgeAPI } from '@/api/profileKnowledge';
import { queryKeys } from '@/api/queryKeys';
import { queryCache } from '@/lib/query/cache';
import { useQuery } from '@/lib/query/useQuery';
import type { ProfileKnowledgeItem } from '@/api/types';
import { useProfileDocuments } from '@/hooks/useProfileDocuments';
import { formatFileSize, isDocumentBusy } from '@/components/documents/documentLabels';
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
  const [isImporting, setIsImporting] = useState(false);
  const [importText, setImportText] = useState('');
  const [importError, setImportError] = useState<string | null>(null);
  const [isSavingImport, setIsSavingImport] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const [editing, setEditing] = useState<ProfileKnowledgeItem | null>(null);
  const [isComposing, setIsComposing] = useState(false);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [deleting, setDeleting] = useState<ProfileKnowledgeItem | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // Profile Documents State
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [deletingDocId, setDeletingDocId] = useState<string | null>(null);
  const [deletingDocName, setDeletingDocName] = useState<string | null>(null);
  const [isDeletingDoc, setIsDeletingDoc] = useState(false);
  const [deleteDocError, setDeleteDocError] = useState<string | null>(null);

  const {
    entries: docEntries,
    isLoading: isDocsLoading,
    listError: docsListError,
    reload: reloadDocs,
    uploadDocument,
    retryDocument,
    deleteDocument,
  } = useProfileDocuments();

  const query = useQuery<ProfileKnowledgeItem[]>({
    key: queryKeys.profileKnowledge(),
    fetcher: ({ signal }) => profileKnowledgeAPI.list({ signal }),
    fallbackMessage: "We couldn't load your background notes.",
  });

  const items = useMemo(() => query.data ?? [], [query.data]);
  const isLoading = query.status === 'pending' || query.status === 'idle';
  const loadError = query.error?.message ?? null;
  const load = query.refetch;

  const setItems = useCallback(
    (updater: (previous: ProfileKnowledgeItem[]) => ProfileKnowledgeItem[]) => {
      queryCache.setData<ProfileKnowledgeItem[]>(queryKeys.profileKnowledge(), (previous) =>
        updater(previous ?? []),
      );
    },
    [],
  );

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
    if (isSaving) {
      return;
    }
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

  async function handleFileUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;

    setIsUploading(true);
    setUploadError(null);
    try {
      await uploadDocument(file);
      setNotice(`Document "${file.name}" uploaded for processing.`);
    } catch (caught) {
      setUploadError(describeError(caught, 'The document could not be uploaded.').message);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  }

  async function handleConfirmDeleteDoc() {
    if (!deletingDocId) return;

    setIsDeletingDoc(true);
    setDeleteDocError(null);
    try {
      await deleteDocument(deletingDocId);
      setNotice('Profile document removed.');
      setDeletingDocId(null);
      setDeletingDocName(null);
    } catch (caught) {
      setDeleteDocError(describeError(caught, 'Document could not be deleted.').message);
    } finally {
      setIsDeletingDoc(false);
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

      {/* Profile Documents Sub-section */}
      <div className={styles.subSection}>
        <div className={styles.sectionHead}>
          <div>
            <h3 className={styles.subSectionHeading}>Background documents</h3>
            <p className={styles.sectionLede}>
              Personal reference documents (syllabi, degree guidelines, formulas) that persist across all your courses.
              Course deletion never touches these files.
            </p>
          </div>
          <div className={styles.headActions}>
            <input
              ref={fileInputRef}
              type="file"
              className={styles.fileInput}
              accept=".pdf,.txt,.md,.markdown,.png,.jpg,.jpeg"
              onChange={(e) => void handleFileUpload(e)}
            />
            <Button
              variant="secondary"
              icon={<Upload aria-hidden="true" />}
              isLoading={isUploading}
              loadingLabel="Uploading"
              onClick={() => fileInputRef.current?.click()}
            >
              Upload document
            </Button>
          </div>
        </div>

        {uploadError ? (
          <Alert tone="destructive" live="alert">
            {uploadError}
          </Alert>
        ) : null}

        {docsListError ? (
          <ErrorState onRetry={() => void reloadDocs()}>{docsListError}</ErrorState>
        ) : null}

        {isDocsLoading ? (
          <div className={styles.stack} aria-hidden="true">
            <Skeleton variant="block" />
          </div>
        ) : docEntries.length === 0 && !docsListError ? (
          <Card elevation="flat" padding="lg">
            <p className={styles.rowBody}>
              No profile documents uploaded yet. Upload a syllabus or reference document to provide deep background context across your studies.
            </p>
          </Card>
        ) : (
          <div className={styles.stack}>
            {docEntries.map((entry) => {
              if (!entry?.document?.id) return null;
              const { document, pending } = entry;
              const busy = isDocumentBusy(document.status);
              const failed = document.status === 'failed';
              const ready = document.status === 'ready';

              return (
                <Card key={document.id} className={styles.row}>
                  <div className={styles.rowText}>
                    <span className={styles.docBadge}>Profile Document</span>
                    <p className={styles.rowTitle}>{document.original_file_name}</p>
                    <div className={styles.docFacts}>
                      <span>{(document.file_type || '').toUpperCase()}</span>
                      <span>·</span>
                      <span>{formatFileSize(document.file_size)}</span>
                      <span>·</span>
                      <span className={styles.docStatus}>
                        {ready ? (
                          <span className={styles.statusReady}>
                            <Check size={14} aria-hidden="true" /> Ready
                          </span>
                        ) : busy ? (
                          <span className={styles.statusBusy}>
                            <Loader2 size={14} className="spin" aria-hidden="true" /> Processing
                          </span>
                        ) : failed ? (
                          <span className={styles.statusFailed}>
                            <AlertTriangle size={14} aria-hidden="true" /> Processing failed
                          </span>
                        ) : (
                          <span>{document.status}</span>
                        )}
                      </span>
                    </div>
                    {document.processing_error ? (
                      <p className={styles.rowBody}>{document.processing_error}</p>
                    ) : null}
                  </div>
                  <div className={styles.rowActions}>
                    {failed ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        icon={<RotateCcw size={14} aria-hidden="true" />}
                        isLoading={pending === 'retry'}
                        loadingLabel="Retrying"
                        onClick={() => void retryDocument(document.id)}
                      >
                        Retry
                      </Button>
                    ) : null}
                    <Button
                      variant="ghost"
                      size="sm"
                      icon={<Trash2 size={14} aria-hidden="true" />}
                      aria-label={`Delete ${document.original_file_name}`}
                      disabled={pending === 'delete'}
                      onClick={() => {
                        setDeleteDocError(null);
                        setDeletingDocId(document.id);
                        setDeletingDocName(document.original_file_name);
                      }}
                    >
                      Delete
                    </Button>
                  </div>
                </Card>
              );
            })}
          </div>
        )}
      </div>

      <Dialog
        open={isComposing}
        onClose={() => setIsComposing(false)}
        title={editing ? 'Edit knowledge topic' : 'Add knowledge topic'}
        description="Student-provided background notes used as supplementary context across your courses."
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

      <ConfirmDialog
        open={deletingDocId !== null}
        onClose={() => {
          setDeletingDocId(null);
          setDeletingDocName(null);
          setDeleteDocError(null);
        }}
        onConfirm={handleConfirmDeleteDoc}
        title={deletingDocName ? `Delete “${deletingDocName}”?` : 'Delete document?'}
        description="This profile background document and its stored vectors will be permanently removed. Your courses are not affected."
        confirmLabel="Delete"
        pendingLabel="Deleting"
        isPending={isDeletingDoc}
      >
        {deleteDocError ? (
          <Alert tone="destructive" live="alert">
            {deleteDocError}
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
