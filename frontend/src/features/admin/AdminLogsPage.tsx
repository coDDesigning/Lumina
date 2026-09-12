import { startTransition, useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Download, RefreshCw, SlidersHorizontal } from 'lucide-react';
import { adminAPI } from '@/api/admin';
import { describeError } from '@/api/errors';
import { queryKeys } from '@/api/queryKeys';
import type {
  AdminLogLevel,
  AdminLogRecord,
  AdminLogSource,
  AdminLogSourceHealth,
} from '@/api/types';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useQuery } from '@/lib/query/useQuery';
import { Alert } from '@/ui/Alert';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { Dialog } from '@/ui/Dialog';
import { EmptyState } from '@/ui/EmptyState';
import { ErrorState } from '@/ui/ErrorState';
import { Input, Select } from '@/ui/Input';
import { PageHeader } from '@/ui/PageHeader';
import { Skeleton } from '@/ui/Skeleton';
import styles from './AdminLogsPage.module.css';

const UI_PARAMS = new Set(['auto', 'columns', 'record']);
const MULTI_TEXT_FILTERS = [
  'service',
  'environment',
  'logger',
  'event',
  'http_method',
  'http_path',
  'error_code',
  'error_category',
  'exception_type',
  'failed_stage',
  'job_type',
  'job_status',
  'generation_type',
  'provider',
  'model',
] as const;
const LEVELS: AdminLogLevel[] = ['INFO', 'WARNING', 'ERROR', 'CRITICAL'];
const SOURCES: Array<{ value: AdminLogSource; label: string }> = [
  { value: 'operational', label: 'Operational' },
  { value: 'ai_telemetry', label: 'AI telemetry' },
  { value: 'client_report', label: 'Client reports' },
];

type Column =
  | 'timestamp'
  | 'level'
  | 'service'
  | 'event'
  | 'description'
  | 'error'
  | 'http'
  | 'duration'
  | 'correlation';

const COLUMNS: Array<{ id: Column; label: string }> = [
  { id: 'timestamp', label: 'Timestamp' },
  { id: 'level', label: 'Level' },
  { id: 'service', label: 'Source / service' },
  { id: 'event', label: 'Event' },
  { id: 'description', label: 'Description' },
  { id: 'error', label: 'Error code' },
  { id: 'http', label: 'HTTP' },
  { id: 'duration', label: 'Duration' },
  { id: 'correlation', label: 'Correlation' },
];
const DEFAULT_COLUMNS = COLUMNS.map((column) => column.id);

function fixedWindow(minutes: number): { start: string; end: string } {
  const end = new Date();
  const start = new Date(end.getTime() - minutes * 60_000);
  return { start: start.toISOString(), end: end.toISOString() };
}

function toLocalInput(value: string): string {
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function formatTimestamp(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'short',
    timeStyle: 'medium',
  }).format(new Date(value));
}

function levelTone(level: AdminLogLevel): 'neutral' | 'warning' | 'destructive' {
  if (level === 'ERROR' || level === 'CRITICAL') return 'destructive';
  if (level === 'WARNING') return 'warning';
  return 'neutral';
}

function sourceLabel(source: AdminLogSource): string {
  return SOURCES.find((item) => item.value === source)?.label ?? source;
}

function apiParams(searchParams: URLSearchParams, includeCursor = true): URLSearchParams {
  const params = new URLSearchParams(searchParams);
  for (const key of UI_PARAMS) params.delete(key);
  if (!includeCursor) params.delete('cursor');
  return params;
}

function commaValues(params: URLSearchParams, key: string): string {
  return params.getAll(key).join(', ');
}

function sourceHealthTone(
  health: AdminLogSourceHealth,
): 'success' | 'warning' | 'destructive' | 'neutral' {
  if (health.status === 'available') return 'success';
  if (health.status === 'delayed' || health.status === 'unconfigured') return 'warning';
  return 'destructive';
}

function identifier(record: AdminLogRecord): string {
  if (record.job_id != null && record.job_type) return `${record.job_type}:${record.job_id}`;
  return record.operation_id ?? record.request_id ?? 'No correlation';
}

function cell(column: Column, record: AdminLogRecord, inspect: () => void): ReactNode {
  switch (column) {
    case 'timestamp':
      return <time dateTime={record.timestamp}>{formatTimestamp(record.timestamp)}</time>;
    case 'level':
      return <Badge tone={levelTone(record.level)}>{record.level}</Badge>;
    case 'service':
      return (
        <span>
          <strong>{record.service}</strong>
          <small>{sourceLabel(record.source)}</small>
        </span>
      );
    case 'event':
      return (
        <Button size="sm" variant="ghost" onClick={inspect}>
          {record.event}
        </Button>
      );
    case 'description':
      return <span className={styles.clamped}>{record.description}</span>;
    case 'error':
      return record.error_code ?? record.error_category ?? '—';
    case 'http':
      return record.http_status
        ? `${record.http_method ?? ''} ${record.http_status}`.trim()
        : '—';
    case 'duration':
      return record.duration_ms == null ? '—' : `${Math.round(record.duration_ms)} ms`;
    case 'correlation':
      return <code className={styles.compactCode}>{identifier(record)}</code>;
  }
}

export default function AdminLogsPage() {
  useDocumentTitle('Admin logs');
  const initialWindow = useRef(fixedWindow(60));
  const [searchParams, setSearchParams] = useSearchParams();
  const [columnsOpen, setColumnsOpen] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exporting, setExporting] = useState<'jsonl' | 'csv' | null>(null);
  const [newRecords, setNewRecords] = useState(0);
  const pollRunning = useRef(false);

  const effectiveParams = useMemo(() => {
    const params = new URLSearchParams(searchParams);
    if (!params.has('start')) params.set('start', initialWindow.current.start);
    if (!params.has('end')) params.set('end', initialWindow.current.end);
    return params;
  }, [searchParams]);

  useEffect(() => {
    if (searchParams.has('start') && searchParams.has('end')) return;
    setSearchParams(effectiveParams, { replace: true });
  }, [effectiveParams, searchParams, setSearchParams]);

  const listParams = apiParams(effectiveParams);
  const summaryParams = apiParams(effectiveParams, false);
  const listQueryString = listParams.toString();
  const summaryQueryString = summaryParams.toString();
  const selectedRecord = effectiveParams.get('record');
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  const configuredColumns = effectiveParams.get('columns')?.split(',') as Column[] | undefined;
  const visibleColumns = (configuredColumns?.filter((column) =>
    COLUMNS.some((available) => available.id === column),
  ) ?? DEFAULT_COLUMNS) as Column[];

  const logsQuery = useQuery({
    key: queryKeys.adminLogs(listQueryString),
    fetcher: ({ signal }) => adminAPI.listLogs(listParams, { signal }),
    fallbackMessage: "We couldn't load operational records.",
    onRefetchError: 'keep',
  });
  const summaryQuery = useQuery({
    key: queryKeys.adminLogSummary(summaryQueryString),
    fetcher: ({ signal }) => adminAPI.summarizeLogs(summaryParams, { signal }),
    fallbackMessage: "We couldn't load the operational summary.",
    onRefetchError: 'keep',
  });
  const detailQuery = useQuery({
    key: selectedRecord ? queryKeys.adminLogEvent(selectedRecord) : null,
    fetcher: ({ signal }) => adminAPI.getLogEvent(selectedRecord ?? '', { signal }),
    fallbackMessage: "We couldn't load that event.",
  });
  const traceQuery = useQuery({
    key: selectedRecord ? queryKeys.adminLogTrace(selectedRecord) : null,
    fetcher: ({ signal }) => adminAPI.traceLogEvent(selectedRecord ?? '', { signal }),
    fallbackMessage: "We couldn't load the operation trace.",
  });

  const firstRecordId = logsQuery.data?.records[0]?.id;
  useEffect(() => {
    if (effectiveParams.get('auto') !== '1') return;
    let disposed = false;
    const poll = async () => {
      if (disposed || document.visibilityState !== 'visible' || pollRunning.current) return;
      pollRunning.current = true;
      try {
        const latestParams = new URLSearchParams(summaryQueryString);
        latestParams.set('limit', '50');
        const latest = await adminAPI.listLogs(latestParams);
        if (firstRecordId && latest.records[0]?.id !== firstRecordId) {
          const knownIndex = latest.records.findIndex((record) => record.id === firstRecordId);
          setNewRecords(knownIndex > 0 ? knownIndex : 1);
        }
      } catch {
        // The visible query keeps its last good result and exposes failures on manual refresh.
      } finally {
        pollRunning.current = false;
      }
    };
    const timer = window.setInterval(() => void poll(), 10_000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [effectiveParams, firstRecordId, summaryQueryString]);

  const activeFilterCount = [...new Set([...summaryParams.keys()])].filter(
    (key) => !['start', 'end'].includes(key),
  ).length;

  function setPreset(minutes: number, extra: Record<string, string[]> = {}) {
    const window = fixedWindow(minutes);
    const next = new URLSearchParams({ start: window.start, end: window.end });
    for (const [key, values] of Object.entries(extra)) {
      for (const value of values) next.append(key, value);
    }
    if (effectiveParams.get('auto') === '1') next.set('auto', '1');
    startTransition(() => setSearchParams(next));
  }

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const next = new URLSearchParams();
    const start = String(form.get('start') ?? '');
    const end = String(form.get('end') ?? '');
    if (start) next.set('start', new Date(start).toISOString());
    if (end) next.set('end', new Date(end).toISOString());
    for (const key of ['level', 'source']) {
      for (const value of form.getAll(key)) next.append(key, String(value));
    }
    for (const key of MULTI_TEXT_FILTERS) {
      const value = String(form.get(key) ?? '');
      for (const part of value.split(',').map((item) => item.trim()).filter(Boolean)) {
        next.append(key, part);
      }
    }
    for (const key of [
      'http_status',
      'http_status_class',
      'minimum_duration_ms',
      'maximum_duration_ms',
      'attempt_number',
      'request_id',
      'operation_id',
      'job_id',
      'user_id',
      'course_id',
      'document_id',
      'search',
      'success',
    ]) {
      const value = String(form.get(key) ?? '').trim();
      if (value) next.set(key, value);
    }
    if (effectiveParams.get('auto') === '1') next.set('auto', '1');
    if (effectiveParams.get('columns')) next.set('columns', effectiveParams.get('columns')!);
    startTransition(() => setSearchParams(next));
  }

  function inspect(record: AdminLogRecord) {
    const next = new URLSearchParams(effectiveParams);
    next.set('record', record.id);
    setSearchParams(next);
  }

  function closeDetail() {
    const next = new URLSearchParams(effectiveParams);
    next.delete('record');
    setSearchParams(next);
  }

  function applyErrorGroup(signature: string) {
    const next = new URLSearchParams(summaryParams);
    next.set('error_signature', signature);
    startTransition(() => setSearchParams(next));
  }

  async function refresh() {
    setNewRecords(0);
    await Promise.all([logsQuery.refetch(), summaryQuery.refetch()]);
  }

  function toggleAutoRefresh() {
    const next = new URLSearchParams(effectiveParams);
    if (next.get('auto') === '1') next.delete('auto');
    else next.set('auto', '1');
    setSearchParams(next, { replace: true });
  }

  function saveColumns(formEvent: FormEvent<HTMLFormElement>) {
    formEvent.preventDefault();
    const selected = new FormData(formEvent.currentTarget).getAll('column').map(String);
    if (selected.length === 0) return;
    const next = new URLSearchParams(effectiveParams);
    next.set('columns', selected.join(','));
    setSearchParams(next, { replace: true });
    setColumnsOpen(false);
  }

  async function exportRecords(format: 'jsonl' | 'csv') {
    setExportError(null);
    setExporting(format);
    try {
      const result = await adminAPI.exportLogs(summaryParams, format);
      const url = URL.createObjectURL(result.blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `lumina-operational-events.${format}`;
      anchor.click();
      URL.revokeObjectURL(url);
      if (result.truncated) {
        setExportError('The export reached the 10,000-record limit and is explicitly marked truncated.');
      }
    } catch (caught) {
      setExportError(describeError(caught, "The export couldn't be created.").message);
    } finally {
      setExporting(null);
    }
  }

  async function copy(value: string) {
    await navigator.clipboard.writeText(value);
  }

  const health = logsQuery.data?.source_health ?? summaryQuery.data?.source_health ?? [];
  const unavailable = health.length > 0 && health.every((item) => item.status === 'unavailable');
  const unconfigured = health.length > 0 && health.every((item) => item.status === 'unconfigured');
  const records = logsQuery.data?.records ?? [];
  const loading = logsQuery.status === 'idle' || logsQuery.status === 'pending';

  return (
    <div className={styles.page}>
      <PageHeader
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Logs' }]}
        badges={<Badge tone="accent">UTC storage</Badge>}
        actions={
          <div className={styles.headerActions}>
            <Button
              size="sm"
              variant="ghost"
              icon={<RefreshCw aria-hidden="true" />}
              isLoading={logsQuery.isFetching || summaryQuery.isFetching}
              loadingLabel="Refreshing logs"
              onClick={() => void refresh()}
            >
              Refresh
            </Button>
            <Button size="sm" variant="ghost" onClick={toggleAutoRefresh}>
              Auto-refresh {effectiveParams.get('auto') === '1' ? 'on' : 'off'}
            </Button>
          </div>
        }
      />

      <div className={styles.body}>
        <div className={styles.intro}>
          <div>
            <p className={styles.eyebrow}>Investigation center</p>
            <h1>Logs and errors</h1>
            <p className={styles.subtitle}>
              Times are stored in UTC and displayed in {timeZone}. Caller-supplied request IDs are
              searchable, but only typed job and operation relationships form a trace.
            </p>
          </div>
          <Badge tone={activeFilterCount ? 'info' : 'neutral'}>
            {activeFilterCount} active {activeFilterCount === 1 ? 'filter' : 'filters'}
          </Badge>
        </div>

        {newRecords > 0 ? (
          <Alert tone="info" live="status" className={styles.notice}>
            {newRecords} new {newRecords === 1 ? 'record is' : 'records are'} available.{' '}
            <Button size="sm" variant="ghost" onClick={() => void refresh()}>
              Load new records
            </Button>
          </Alert>
        ) : null}

        <section className={styles.presets} aria-label="Time and investigation presets">
          <Button size="sm" variant="ghost" onClick={() => setPreset(15)}>Last 15 minutes</Button>
          <Button size="sm" variant="ghost" onClick={() => setPreset(60)}>1 hour</Button>
          <Button size="sm" variant="ghost" onClick={() => setPreset(1_440)}>24 hours</Button>
          <Button size="sm" variant="ghost" onClick={() => setPreset(10_080)}>7 days</Button>
          <Button size="sm" variant="ghost" onClick={() => setPreset(60, { level: ['ERROR', 'CRITICAL'] })}>Errors</Button>
          <Button size="sm" variant="ghost" onClick={() => setPreset(1_440, { job_status: ['failed'] })}>Failed jobs</Button>
          <Button size="sm" variant="ghost" onClick={() => setPreset(1_440, { source: ['ai_telemetry'], success: ['false'] })}>AI provider errors</Button>
          <Button size="sm" variant="ghost" onClick={() => setPreset(60, { minimum_duration_ms: ['2000'] })}>Slow requests</Button>
        </section>

        <form key={summaryQueryString} className={styles.filters} onSubmit={applyFilters}>
          <div className={styles.primaryFilters}>
            <Input label="Start time" name="start" type="datetime-local" defaultValue={toLocalInput(effectiveParams.get('start')!)} />
            <Input label="End time" name="end" type="datetime-local" defaultValue={toLocalInput(effectiveParams.get('end')!)} />
            <Input label="Service" name="service" placeholder="api, worker" defaultValue={commaValues(effectiveParams, 'service')} />
            <Input label="Error code" name="error_code" defaultValue={commaValues(effectiveParams, 'error_code')} />
            <Input label="Request ID" name="request_id" defaultValue={effectiveParams.get('request_id') ?? ''} />
            <Input label="Operation ID" name="operation_id" defaultValue={effectiveParams.get('operation_id') ?? ''} />
            <Input label="Search safe fields" name="search" type="search" maxLength={200} defaultValue={effectiveParams.get('search') ?? ''} />
          </div>

          <div className={styles.checkGroups}>
            <fieldset>
              <legend>Levels</legend>
              {LEVELS.map((level) => (
                <label key={level}>
                  <input type="checkbox" name="level" value={level} defaultChecked={effectiveParams.getAll('level').includes(level)} />
                  {level}
                </label>
              ))}
            </fieldset>
            <fieldset>
              <legend>Sources</legend>
              {SOURCES.map((source) => (
                <label key={source.value}>
                  <input type="checkbox" name="source" value={source.value} defaultChecked={effectiveParams.getAll('source').includes(source.value)} />
                  {source.label}
                </label>
              ))}
            </fieldset>
          </div>

          <details className={styles.advanced}>
            <summary>Advanced filters</summary>
            <div className={styles.advancedGrid}>
              {MULTI_TEXT_FILTERS.filter((key) => !['service', 'error_code'].includes(key)).map((key) => (
                <Input key={key} label={key.replaceAll('_', ' ')} name={key} defaultValue={commaValues(effectiveParams, key)} />
              ))}
              <Input label="HTTP status" name="http_status" inputMode="numeric" defaultValue={effectiveParams.get('http_status') ?? ''} />
              <Input label="HTTP status class" name="http_status_class" inputMode="numeric" defaultValue={effectiveParams.get('http_status_class') ?? ''} />
              <Input label="Minimum duration (ms)" name="minimum_duration_ms" inputMode="decimal" defaultValue={effectiveParams.get('minimum_duration_ms') ?? ''} />
              <Input label="Maximum duration (ms)" name="maximum_duration_ms" inputMode="decimal" defaultValue={effectiveParams.get('maximum_duration_ms') ?? ''} />
              <Input label="Attempt number" name="attempt_number" inputMode="numeric" defaultValue={effectiveParams.get('attempt_number') ?? ''} />
              <Input label="Job ID" name="job_id" inputMode="numeric" defaultValue={effectiveParams.get('job_id') ?? ''} />
              <Input label="User ID" name="user_id" inputMode="numeric" defaultValue={effectiveParams.get('user_id') ?? ''} />
              <Input label="Course ID" name="course_id" inputMode="numeric" defaultValue={effectiveParams.get('course_id') ?? ''} />
              <Input label="Document ID" name="document_id" defaultValue={effectiveParams.get('document_id') ?? ''} />
              <Select label="AI outcome" name="success" defaultValue={effectiveParams.get('success') ?? ''}>
                <option value="">Any outcome</option>
                <option value="true">Succeeded</option>
                <option value="false">Failed</option>
              </Select>
            </div>
          </details>

          <div className={styles.filterActions}>
            <Button variant="primary" type="submit">Apply filters</Button>
            <Button variant="ghost" onClick={() => setPreset(60)}>Clear all</Button>
          </div>
        </form>

        <section className={styles.healthSection} aria-labelledby="source-health-title">
          <div className={styles.sectionHeading}>
            <div>
              <h2 id="source-health-title">Source health</h2>
              <p>
                Covered {formatTimestamp(effectiveParams.get('start')!)} to{' '}
                {formatTimestamp(effectiveParams.get('end')!)}
              </p>
            </div>
            {logsQuery.data ? (
              <span>Last successful fetch {formatTimestamp(logsQuery.data.last_updated_at)}</span>
            ) : null}
          </div>
          <div className={styles.healthGrid}>
            {health.map((item) => (
              <article key={item.source} className={styles.healthCard}>
                <div>
                  <strong>{sourceLabel(item.source)}</strong>
                  <Badge tone={sourceHealthTone(item)}>{item.status}</Badge>
                </div>
                <p>{item.detail ?? (item.ingestion_delay_seconds == null ? 'No event has arrived yet.' : `${Math.round(item.ingestion_delay_seconds)}s ingestion delay`)}</p>
                {item.dropped_records ? <span>{item.dropped_records} dropped records</span> : null}
                {item.malformed_records ? <span>{item.malformed_records} malformed records skipped</span> : null}
              </article>
            ))}
          </div>
        </section>

        <section className={styles.summarySection} aria-labelledby="summary-title">
          <div className={styles.sectionHeading}>
            <h2 id="summary-title">Scope summary</h2>
            {summaryQuery.data?.partial ? <Badge tone="warning">Partial</Badge> : null}
          </div>
          {summaryQuery.status === 'pending' || summaryQuery.status === 'idle' ? (
            <Skeleton variant="block" />
          ) : summaryQuery.error ? (
            <ErrorState onRetry={() => void summaryQuery.refetch()}>{summaryQuery.error.message}</ErrorState>
          ) : summaryQuery.data ? (
            <>
              <div className={styles.metrics}>
                {[
                  ['Events', summaryQuery.data.counts.events],
                  ['Warnings', summaryQuery.data.counts.warnings],
                  ['Errors', summaryQuery.data.counts.errors],
                  ['Failed operations', summaryQuery.data.counts.distinct_failed_operations],
                ].map(([label, value]) => (
                  <div key={String(label)}><span>{label}</span><strong>{value ?? 'Unavailable'}</strong></div>
                ))}
              </div>
              {summaryQuery.data.distribution.length > 0 ? (
                <div className={styles.distribution} aria-label="Event distribution over time">
                  {summaryQuery.data.distribution.map((bucket) => (
                    <div key={bucket.start}>
                      <time dateTime={bucket.start}>{formatTimestamp(bucket.start)}</time>
                      <meter min={0} max={Math.max(...summaryQuery.data!.distribution.map((item) => item.events))} value={bucket.events}>{bucket.events}</meter>
                      <span>{bucket.events} events · {bucket.errors} errors</span>
                    </div>
                  ))}
                </div>
              ) : null}
              {summaryQuery.data.error_groups.length > 0 ? (
                <div className={styles.groups}>
                  <h3>Frequent error groups</h3>
                  {summaryQuery.data.error_groups.map((group) => (
                    <button key={group.signature} type="button" onClick={() => applyErrorGroup(group.signature)}>
                      <span><strong>{group.event}</strong><small>{group.error_code ?? group.exception_type ?? group.signature}</small></span>
                      <span>{group.event_count} events · {group.distinct_operations ?? 'Unknown'} operations</span>
                    </button>
                  ))}
                </div>
              ) : null}
            </>
          ) : null}
        </section>

        {logsQuery.error ? (
          <ErrorState onRetry={() => void logsQuery.refetch()}>{logsQuery.error.message}</ErrorState>
        ) : null}
        {exportError ? (
          <ErrorState actions={<Button size="sm" onClick={() => void exportRecords('jsonl')}>Retry JSONL export</Button>}>{exportError}</ErrorState>
        ) : null}

        <section className={styles.recordsSection} aria-labelledby="records-title">
          <div className={styles.sectionHeading}>
            <div><h2 id="records-title">Records</h2><p>Newest first · up to 50 per page</p></div>
            <div className={styles.recordActions}>
              <Button size="sm" variant="ghost" icon={<SlidersHorizontal aria-hidden="true" />} onClick={() => setColumnsOpen(true)}>Columns</Button>
              <Button size="sm" variant="ghost" icon={<Download aria-hidden="true" />} isLoading={exporting === 'jsonl'} loadingLabel="Exporting JSONL" onClick={() => void exportRecords('jsonl')}>JSONL</Button>
              <Button size="sm" variant="ghost" icon={<Download aria-hidden="true" />} isLoading={exporting === 'csv'} loadingLabel="Exporting CSV" onClick={() => void exportRecords('csv')}>CSV</Button>
            </div>
          </div>

          {logsQuery.error ? null : loading ? (
            <Skeleton variant="block" />
          ) : unavailable ? (
            <ErrorState onRetry={() => void refresh()}>Every selected source is unavailable.</ErrorState>
          ) : unconfigured ? (
            <EmptyState title="Source is not configured" description="Configure the deployment's operational source, then retry." />
          ) : records.length === 0 ? (
            <EmptyState
              title={activeFilterCount ? 'No records match' : 'No records in this period'}
              description={health.some((item) => item.available_from && new Date(item.available_from) > new Date(effectiveParams.get('end')!)) ? 'Records for this period have expired under source retention.' : 'Try a wider time range or clear filters.'}
            />
          ) : (
            <div className={styles.tableWrap} role="region" aria-label="Operational records" tabIndex={0}>
              <table>
                <thead><tr>{visibleColumns.map((column) => <th key={column}>{COLUMNS.find((item) => item.id === column)?.label}</th>)}</tr></thead>
                <tbody>
                  {records.map((record) => (
                    <tr key={record.id}>
                      {visibleColumns.map((column) => <td key={column}>{cell(column, record, () => inspect(record))}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className={styles.pagination}>
            {effectiveParams.has('cursor') ? <Button variant="ghost" onClick={() => window.history.back()}>Previous page</Button> : <span />}
            {logsQuery.data?.next_cursor ? (
              <Button onClick={() => {
                const next = new URLSearchParams(effectiveParams);
                next.set('cursor', logsQuery.data!.next_cursor!);
                setSearchParams(next);
              }}>Next page</Button>
            ) : null}
          </div>
        </section>
      </div>

      <Dialog
        open={columnsOpen}
        onClose={() => setColumnsOpen(false)}
        title="Visible columns"
        size="sm"
        footer={<Button type="submit" form="log-columns-form" variant="primary">Apply columns</Button>}
      >
        <form id="log-columns-form" className={styles.columnForm} onSubmit={saveColumns}>
          {COLUMNS.map((column) => (
            <label key={column.id}><input type="checkbox" name="column" value={column.id} defaultChecked={visibleColumns.includes(column.id)} />{column.label}</label>
          ))}
        </form>
      </Dialog>

      <Dialog
        open={selectedRecord !== null}
        onClose={closeDetail}
        title="Operational event"
        description={detailQuery.data?.record.event}
        size="xl"
      >
        {detailQuery.status === 'pending' || detailQuery.status === 'idle' ? (
          <Skeleton variant="block" />
        ) : detailQuery.error ? (
          <ErrorState onRetry={() => void detailQuery.refetch()}>{detailQuery.error.message}</ErrorState>
        ) : detailQuery.data ? (
          <div className={styles.detail}>
            <div className={styles.detailHeading}>
              <Badge tone={levelTone(detailQuery.data.record.level)}>{detailQuery.data.record.level}</Badge>
              <time dateTime={detailQuery.data.record.timestamp}>{formatTimestamp(detailQuery.data.record.timestamp)}</time>
              <span>{detailQuery.data.record.environment} / {detailQuery.data.record.service}</span>
            </div>
            <p>{detailQuery.data.record.description}</p>
            <dl>
              {Object.entries({
                Logger: detailQuery.data.record.logger,
                'Error code': detailQuery.data.record.error_code,
                'Error category': detailQuery.data.record.error_category,
                Exception: detailQuery.data.record.exception_type,
                'Exception chain': detailQuery.data.record.exception_chain.join(' → ') || null,
                Location: detailQuery.data.record.source_location,
                Route: detailQuery.data.record.http_path,
                User: detailQuery.data.record.user_id,
                Course: detailQuery.data.record.course_id,
                Document: detailQuery.data.record.document_id,
                Auth: detailQuery.data.record.details?.auth_state ?? null,
                'HTTP outcome': detailQuery.data.record.http_status,
                Duration: detailQuery.data.record.duration_ms == null ? null : `${detailQuery.data.record.duration_ms} ms`,
                Stage: detailQuery.data.record.failed_stage ?? detailQuery.data.record.stage,
                Provider: detailQuery.data.record.provider,
                Model: detailQuery.data.record.model,
                Tokens: detailQuery.data.record.total_tokens,
                'Estimated cost': detailQuery.data.record.estimated_cost_usd,
                'Request ID': detailQuery.data.record.request_id,
                'Operation ID': detailQuery.data.record.operation_id,
                Job: detailQuery.data.record.job_id == null ? null : `${detailQuery.data.record.job_type}:${detailQuery.data.record.job_id}`,
              }).filter(([, value]) => value != null).map(([label, value]) => (
                <div key={label}><dt>{label}</dt><dd>{String(value)}</dd></div>
              ))}
            </dl>
            <div className={styles.copyActions}>
              {detailQuery.data.record.operation_id ? <Button size="sm" variant="ghost" onClick={() => void copy(detailQuery.data!.record.operation_id!)}>Copy operation ID</Button> : null}
              {detailQuery.data.record.request_id ? <Button size="sm" variant="ghost" onClick={() => void copy(detailQuery.data!.record.request_id!)}>Copy request ID</Button> : null}
              <Button size="sm" variant="ghost" onClick={() => void copy(JSON.stringify(detailQuery.data!.record, null, 2))}>Copy sanitized JSON</Button>
            </div>
            <details><summary>Sanitized JSON</summary><pre>{JSON.stringify(detailQuery.data.record, null, 2)}</pre></details>
            <section aria-labelledby="trace-title">
              <h3 id="trace-title">Operation timeline</h3>
              {traceQuery.status === 'pending' || traceQuery.status === 'idle' ? <Skeleton /> : traceQuery.error ? <ErrorState onRetry={() => void traceQuery.refetch()}>{traceQuery.error.message}</ErrorState> : traceQuery.data?.correlation_status === 'none' ? <EmptyState title="No correlation information" description="This record has no authoritative operation or typed job relationship." /> : (
                <ol className={styles.timeline}>
                  {traceQuery.data?.records.map((record) => (
                    <li key={record.id}>
                      <time dateTime={record.timestamp}>{formatTimestamp(record.timestamp)}</time>
                      <span><strong>{record.event}</strong><small>Attempt {record.attempt_number ?? '—'} · {record.job_status ?? record.level}</small></span>
                    </li>
                  ))}
                </ol>
              )}
            </section>
            {detailQuery.data.record.error_signature ? (
              <Button
                variant="secondary"
                onClick={() => applyErrorGroup(detailQuery.data!.record.error_signature!)}
              >
                Inspect similar errors
              </Button>
            ) : null}
          </div>
        ) : null}
      </Dialog>
    </div>
  );
}
