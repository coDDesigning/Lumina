import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { APIError } from '../api/client';
import { profileDocumentsAPI } from '../api/profileDocuments';
import { queryKeys } from '../api/queryKeys';
import { queryCache } from '../lib/query/cache';
import { useQuery } from '../lib/query/useQuery';
import { describeDocumentError, describeError, isAbortError } from '../api/errors';
import type {
  ProfileDocumentResponse,
  ProfileDocumentStatusResponse,
  ProcessingJobResponse,
} from '../api/types';
import { isTerminalDocumentStatus } from '../components/documents/documentLabels';

export type DocumentPendingAction = 'retry' | 'delete';

export interface ProfileDocumentEntry {
  document: ProfileDocumentResponse;
  job: ProcessingJobResponse | null;
  error: string | null;
  pending: DocumentPendingAction | null;
}

export interface UseProfileDocumentsResult {
  entries: ProfileDocumentEntry[];
  isLoading: boolean;
  listError: string | null;
  readyCount: number;
  reload: () => void;
  uploadDocument: (file: File) => Promise<void>;
  addUploaded: (document: ProfileDocumentResponse) => void;
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

function isNewer(
  incoming: ProfileDocumentResponse,
  stored: ProfileDocumentResponse,
): boolean {
  const incomingAt = Date.parse(incoming.updated_at);
  const storedAt = Date.parse(stored.updated_at);

  if (Number.isNaN(incomingAt) || Number.isNaN(storedAt)) return true;
  if (incomingAt !== storedAt) return incomingAt > storedAt;

  return (STATUS_RANK[incoming.status] ?? 0) >= (STATUS_RANK[stored.status] ?? 0);
}

function newEntry(document: ProfileDocumentResponse): ProfileDocumentEntry {
  return { document, job: null, error: null, pending: null };
}

function mergeListing(
  previous: ProfileDocumentEntry[],
  documents: ProfileDocumentResponse[],
): ProfileDocumentEntry[] {
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

export function useProfileDocuments(): UseProfileDocumentsResult {
  const [entries, setEntries] = useState<ProfileDocumentEntry[]>([]);

  const controlRef = useRef<PollControl | null>(null);
  const seedRef = useRef<((documents: ProfileDocumentResponse[]) => void) | null>(null);
  const entriesRef = useRef<ProfileDocumentEntry[]>(entries);
  entriesRef.current = entries;

  const listing = useQuery<ProfileDocumentResponse[]>({
    key: queryKeys.profileDocuments(),
    fetcher: ({ signal }) => profileDocumentsAPI.list({ signal }),
    fallbackMessage: 'Profile documents could not be loaded.',
  });

  const isLoading = listing.status === 'pending' || listing.status === 'idle';
  const listError = listing.error?.message ?? null;

  useEffect(() => {
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

    const applyStatus = (
      documentId: string,
      status: ProfileDocumentStatusResponse,
    ) => {
      if (!status?.document?.updated_at) {
        return;
      }
      setEntries((previous) =>
        previous.map((entry) => {
          if (entry.document.id !== documentId) return entry;
          const nextDocument = isNewer(status.document, entry.document)
            ? status.document
            : entry.document;
          return {
            ...entry,
            document: nextDocument,
            job: status.processing_job ?? entry.job,
            error: null,
          };
        }),
      );
    };

    const pollOnce = async (documentId: string) => {
      if (cancelled || inFlight.has(documentId)) return;
      inFlight.add(documentId);

      const attempt = attempts.get(documentId) ?? 0;
      const failCount = failures.get(documentId) ?? 0;

      try {
        const result = await profileDocumentsAPI.getStatus(documentId, {
          signal: controller.signal,
        });
        if (cancelled) return;

        failures.set(documentId, 0);
        applyStatus(documentId, result);

        if (isTerminalDocumentStatus(result.document.status)) {
          stop(documentId);
          void queryCache.invalidate(queryKeys.profileDocuments());
          return;
        }

        attempts.set(documentId, attempt + 1);
        schedule(documentId, delayFor(POLL_DELAYS_MS, attempt));
      } catch (error) {
        if (cancelled || isAbortError(error)) return;

        if (error instanceof APIError && error.status === 404) {
          stop(documentId);
          setEntries((previous) =>
            previous.filter((entry) => entry.document.id !== documentId),
          );
          queryCache.invalidatePrefix(queryKeys.profileDocuments());
          return;
        }

        const nextFailures = failCount + 1;
        failures.set(documentId, nextFailures);

        if (nextFailures >= MAX_CONSECUTIVE_FAILURES) {
          stop(documentId);
          const message = describeError(
            error,
            'Document status could not be verified.',
          );
          setEntries((previous) =>
            previous.map((entry) =>
              entry.document.id === documentId
                ? { ...entry, error: message }
                : entry,
            ),
          );
          return;
        }

        schedule(documentId, delayFor(ERROR_DELAYS_MS, nextFailures - 1));
      } finally {
        inFlight.delete(documentId);
      }
    };

    controlRef.current = { signal: controller.signal, schedule, stop };

    seedRef.current = (documents: ProfileDocumentResponse[]) => {
      let stagger = 0;
      for (const document of documents) {
        if (isTerminalDocumentStatus(document.status)) continue;
        if (timers.has(document.id) || inFlight.has(document.id)) continue;
        attempts.set(document.id, 0);
        failures.set(document.id, 0);
        schedule(document.id, stagger);
        stagger += SEED_STAGGER_MS;
      }
    };

    return () => {
      cancelled = true;
      controller.abort();
      for (const timer of timers.values()) clearTimeout(timer);
      timers.clear();
      attempts.clear();
      failures.clear();
      inFlight.clear();
      controlRef.current = null;
      seedRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (listing.data === undefined) return;
    setEntries((previous) => {
      const merged = mergeListing(previous, listing.data ?? []);
      seedRef.current?.(listing.data ?? []);
      return merged;
    });
  }, [listing.data]);

  const addUploaded = useCallback((document: ProfileDocumentResponse) => {
    setEntries((previous) => {
      const exists = previous.some((entry) => entry.document.id === document.id);
      if (exists) {
        return previous.map((entry) =>
          entry.document.id === document.id ? { ...entry, document, error: null } : entry,
        );
      }
      return [newEntry(document), ...previous];
    });
    if (!isTerminalDocumentStatus(document.status)) {
      controlRef.current?.schedule(document.id, 0);
    }
  }, []);

  const uploadDocument = useCallback(
    async (file: File): Promise<void> => {
      const response = await profileDocumentsAPI.upload(file);
      addUploaded(response.document);
      void queryCache.invalidate(queryKeys.profileDocuments());
    },
    [addUploaded],
  );

  const retryDocument = useCallback(async (documentId: string): Promise<void> => {
    setEntries((previous) =>
      previous.map((entry) =>
        entry.document.id === documentId
          ? {
              ...entry,
              pending: 'retry',
              error: null,
              document: { ...entry.document, status: 'uploaded' },
            }
          : entry,
      ),
    );

    try {
      const result = await profileDocumentsAPI.retry(documentId);
      setEntries((previous) =>
        previous.map((entry) =>
          entry.document.id === documentId
            ? {
                ...entry,
                pending: null,
                document: result.document,
                job: result.processing_job ?? entry.job,
                error: null,
              }
            : entry,
        ),
      );
      controlRef.current?.schedule(documentId, 0);
    } catch (error) {
      const message = describeDocumentError(
        error,
        'Processing could not be retried.',
      );
      setEntries((previous) =>
        previous.map((entry) =>
          entry.document.id === documentId
            ? {
                ...entry,
                pending: null,
                error: message,
                document: { ...entry.document, status: 'failed' },
              }
            : entry,
        ),
      );
      throw error;
    }
  }, []);

  const deleteDocument = useCallback(
    async (documentId: string): Promise<void> => {
      controlRef.current?.stop(documentId);

      const snapshot = entriesRef.current.find(
        (entry) => entry.document.id === documentId,
      );

      setEntries((previous) =>
        previous.map((entry) =>
          entry.document.id === documentId
            ? {
                ...entry,
                pending: 'delete',
                document: { ...entry.document, status: 'deleting' },
              }
            : entry,
        ),
      );

      try {
        await profileDocumentsAPI.delete(documentId);
        setEntries((previous) =>
          previous.filter((entry) => entry.document.id !== documentId),
        );
        void queryCache.invalidate(queryKeys.profileDocuments());
      } catch (error) {
        const message = describeDocumentError(
          error,
          'Document could not be deleted.',
        );
        setEntries((previous) =>
          previous.map((entry) =>
            entry.document.id === documentId
              ? snapshot
                ? { ...snapshot, error: message, pending: null }
                : { ...entry, error: message, pending: null }
              : entry,
          ),
        );
        throw error;
      }
    },
    [],
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
    reload: listing.refetch,
    uploadDocument,
    addUploaded,
    retryDocument,
    deleteDocument,
  };
}
