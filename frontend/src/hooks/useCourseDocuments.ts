import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { APIError } from '../api/client';
import { coursesAPI } from '../api/courses';
import { describeDocumentError, describeError, isAbortError } from '../api/errors';
import type {
  DocumentResponse,
  DocumentStatusResponse,
  ProcessingJobResponse,
} from '../api/types';
import { isTerminalDocumentStatus } from '../components/documents/documentLabels';

export type DocumentPendingAction = 'retry' | 'delete';

export interface DocumentEntry {
  document: DocumentResponse;
  job: ProcessingJobResponse | null;
  error: string | null;
  pending: DocumentPendingAction | null;
}

export interface UseCourseDocumentsResult {
  entries: DocumentEntry[];
  isLoading: boolean;
  listError: string | null;
  readyCount: number;
  reload: () => void;
  addUploaded: (document: DocumentResponse) => void;
  retryDocument: (documentId: string) => Promise<void>;
  deleteDocument: (documentId: string) => Promise<void>;
}

interface PollControl {
  signal: AbortSignal;
  schedule: (documentId: string, delayMs: number) => void;
  stop: (documentId: string) => void;
}

const POLL_DELAYS_MS = [1500, 2000, 2000, 3000, 4000, 5000, 8000] as const;
const ERROR_DELAYS_MS = [3000, 6000, 12000, 20000] as const;
const MAX_CONSECUTIVE_FAILURES = 5;
const SEED_STAGGER_MS = 150;

const STATUS_RANK: Record<string, number> = {
  uploaded: 0,
  processing: 1,
  ready: 2,
  failed: 2,
  deleting: 3,
};

function delayFor(table: readonly number[], attempt: number): number {
  return table[Math.min(attempt, table.length - 1)];
}

function isNewer(incoming: DocumentResponse, stored: DocumentResponse): boolean {
  const incomingAt = Date.parse(incoming.updated_at);
  const storedAt = Date.parse(stored.updated_at);

  if (Number.isNaN(incomingAt) || Number.isNaN(storedAt)) return true;
  if (incomingAt !== storedAt) return incomingAt > storedAt;

  return (STATUS_RANK[incoming.status] ?? 0) >= (STATUS_RANK[stored.status] ?? 0);
}

function newEntry(document: DocumentResponse): DocumentEntry {
  return { document, job: null, error: null, pending: null };
}

function mergeListing(
  previous: DocumentEntry[],
  documents: DocumentResponse[],
): DocumentEntry[] {
  const known = new Map(previous.map((entry) => [entry.document.id, entry]));
  const serverIds = new Set(documents.map((document) => document.id));

  const merged = documents.map((document) => {
    const existing = known.get(document.id);
    if (!existing) return newEntry(document);
    return isNewer(document, existing.document) ? { ...existing, document } : existing;
  });

  const optimistic = previous.filter((entry) => !serverIds.has(entry.document.id));

  return [...optimistic, ...merged];
}

export function useCourseDocuments(courseId: number): UseCourseDocumentsResult {
  const [entries, setEntries] = useState<DocumentEntry[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const controlRef = useRef<PollControl | null>(null);
  const entriesRef = useRef<DocumentEntry[]>(entries);
  entriesRef.current = entries;

  const hasValidCourse = Number.isInteger(courseId) && courseId > 0;

  useEffect(() => {
    if (!hasValidCourse) {
      setEntries([]);
      setIsLoading(false);
      setListError(null);
      return;
    }

    const controller = new AbortController();
    const timers = new Map<string, ReturnType<typeof setTimeout>>();
    const inFlight = new Set<string>();
    const attempts = new Map<string, number>();
    const failures = new Map<string, number>();
    let cancelled = false;

    const stop = (documentId: string) => {
      const timer = timers.get(documentId);
      if (timer !== undefined) clearTimeout(timer);
      timers.delete(documentId);
      attempts.delete(documentId);
      failures.delete(documentId);
      inFlight.delete(documentId);
    };

    const schedule = (documentId: string, delayMs: number) => {
      if (cancelled) return;
      const existing = timers.get(documentId);
      if (existing !== undefined) clearTimeout(existing);
      timers.set(
        documentId,
        setTimeout(() => {
          timers.delete(documentId);
          void pollOnce(documentId);
        }, delayMs),
      );
    };

    const applyStatus = (documentId: string, status: DocumentStatusResponse) => {
      setEntries((previous) =>
        previous.map((entry) => {
          if (entry.document.id !== documentId) return entry;
          if (!isNewer(status.document, entry.document)) return entry;
          return {
            ...entry,
            document: status.document,
            job: status.processing_job,
            error: null,
          };
        }),
      );
    };

    const removeEntry = (documentId: string) => {
      setEntries((previous) =>
        previous.filter((entry) => entry.document.id !== documentId),
      );
    };

    const setRowError = (documentId: string, message: string) => {
      setEntries((previous) =>
        previous.map((entry) =>
          entry.document.id === documentId
            ? { ...entry, error: message, pending: null }
            : entry,
        ),
      );
    };

    async function pollOnce(documentId: string): Promise<void> {
      if (cancelled || inFlight.has(documentId)) return;
      inFlight.add(documentId);

      try {
        const status = await coursesAPI.getDocumentStatus(courseId, documentId, {
          signal: controller.signal,
        });
        if (cancelled) return;

        failures.delete(documentId);
        applyStatus(documentId, status);

        if (isTerminalDocumentStatus(status.document.status)) {
          attempts.delete(documentId);
          return;
        }

        const nextAttempt = (attempts.get(documentId) ?? 0) + 1;
        attempts.set(documentId, nextAttempt);
        schedule(documentId, delayFor(POLL_DELAYS_MS, nextAttempt));
      } catch (error) {
        if (cancelled || isAbortError(error)) return;

        if (error instanceof APIError) {
          if (error.status === 404) {
            removeEntry(documentId);
            stop(documentId);
            return;
          }
          if (error.status === 401 || error.status === 403) {
            stop(documentId);
            return;
          }
        }

        const failureCount = (failures.get(documentId) ?? 0) + 1;
        failures.set(documentId, failureCount);

        if (failureCount >= MAX_CONSECUTIVE_FAILURES) {
          setRowError(
            documentId,
            describeError(error, 'Status updates stopped. Reload to try again.').message,
          );
          stop(documentId);
          return;
        }

        schedule(documentId, delayFor(ERROR_DELAYS_MS, failureCount - 1));
      } finally {
        inFlight.delete(documentId);
      }
    }

    async function loadList(): Promise<void> {
      try {
        const documents = await coursesAPI.listDocuments(courseId, {
          signal: controller.signal,
        });
        if (cancelled) return;

        setEntries((previous) => mergeListing(previous, documents));
        setIsLoading(false);

        documents
          .filter((document) => document.status !== 'ready')
          .forEach((document, index) => {
            schedule(document.id, index * SEED_STAGGER_MS);
          });
      } catch (error) {
        if (cancelled || isAbortError(error)) return;
        setIsLoading(false);
        setListError(describeError(error, 'Sources could not be loaded.').message);
      }
    }

    controlRef.current = { signal: controller.signal, schedule, stop };

    setEntries([]);
    setIsLoading(true);
    setListError(null);
    void loadList();

    return () => {
      cancelled = true;
      controlRef.current = null;
      controller.abort();
      timers.forEach((timer) => clearTimeout(timer));
      timers.clear();
      inFlight.clear();
    };
  }, [courseId, hasValidCourse, reloadToken]);

  const reload = useCallback(() => {
    setReloadToken((token) => token + 1);
  }, []);

  const addUploaded = useCallback((document: DocumentResponse) => {
    setEntries((previous) => {
      const index = previous.findIndex((entry) => entry.document.id === document.id);
      if (index === -1) {
        return [newEntry(document), ...previous];
      }
      const next = [...previous];
      next[index] = { ...next[index], document, error: null, pending: null };
      return next;
    });

    if (document.status !== 'ready') {
      controlRef.current?.schedule(document.id, 0);
    }
  }, []);

  const setPending = useCallback(
    (documentId: string, pending: DocumentPendingAction | null) => {
      setEntries((previous) =>
        previous.map((entry) =>
          entry.document.id === documentId
            ? { ...entry, pending, error: pending ? null : entry.error }
            : entry,
        ),
      );
    },
    [],
  );

  const retryDocument = useCallback(
    async (documentId: string) => {
      const control = controlRef.current;
      const entry = entriesRef.current.find((row) => row.document.id === documentId);
      if (!control || !entry || entry.pending) return;

      setPending(documentId, 'retry');

      try {
        const status = await coursesAPI.retryDocument(courseId, documentId, {
          signal: control.signal,
        });
        setEntries((previous) =>
          previous.map((row) =>
            row.document.id === documentId
              ? {
                  document: status.document,
                  job: status.processing_job,
                  error: null,
                  pending: null,
                }
              : row,
          ),
        );
        control.schedule(documentId, POLL_DELAYS_MS[0]);
      } catch (error) {
        if (isAbortError(error)) return;

        const described = describeDocumentError(error, 'The retry could not be started.');

        if (described.status === 404) {
          setEntries((previous) =>
            previous.filter((row) => row.document.id !== documentId),
          );
          control.stop(documentId);
          return;
        }

        setEntries((previous) =>
          previous.map((row) =>
            row.document.id === documentId
              ? { ...row, pending: null, error: described.message }
              : row,
          ),
        );

        if (described.status === 409) {
          control.schedule(documentId, 0);
        }
      }
    },
    [courseId, setPending],
  );

  const deleteDocument = useCallback(
    async (documentId: string) => {
      const control = controlRef.current;
      const entry = entriesRef.current.find((row) => row.document.id === documentId);
      if (!control || !entry || entry.pending) return;

      setPending(documentId, 'delete');

      try {
        await coursesAPI.deleteDocument(courseId, documentId, {
          signal: control.signal,
        });
        control.stop(documentId);
        setEntries((previous) =>
          previous.filter((row) => row.document.id !== documentId),
        );
      } catch (error) {
        if (isAbortError(error)) return;

        const described = describeDocumentError(error, 'The source could not be removed.');

        if (described.status === 404) {
          control.stop(documentId);
          setEntries((previous) =>
            previous.filter((row) => row.document.id !== documentId),
          );
          return;
        }

        setEntries((previous) =>
          previous.map((row) =>
            row.document.id === documentId
              ? { ...row, pending: null, error: described.message }
              : row,
          ),
        );

        if (described.status === 409) {
          control.schedule(documentId, 0);
        }
      }
    },
    [courseId, setPending],
  );

  const readyCount = useMemo(
    () => entries.filter((entry) => entry.document.status === 'ready').length,
    [entries],
  );

  return {
    entries,
    isLoading,
    listError,
    readyCount,
    reload,
    addUploaded,
    retryDocument,
    deleteDocument,
  };
}
