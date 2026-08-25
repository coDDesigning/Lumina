import { useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { adminAPI } from '@/api/admin';
import { queryKeys } from '@/api/queryKeys';
import { queryCache } from '@/lib/query/cache';
import { useQuery } from '@/lib/query/useQuery';
import {
  ADMIN_CREDIT_REASONS,
  POSITIVE_ONLY_ADMIN_REASONS,
  formatDelta,
  reasonLabel,
  transactionLabel,
} from '@/api/creditLabels';
import { describeError } from '@/api/errors';
import type { AdminCreditReason, AiCostReport, CreditTransaction, User } from '@/api/types';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useAuth } from '@/context/AuthContext';
import { Alert } from '@/ui/Alert';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { ConfirmDialog } from '@/ui/ConfirmDialog';
import { Dialog } from '@/ui/Dialog';
import { EmptyState } from '@/ui/EmptyState';
import { ErrorState } from '@/ui/ErrorState';
import { Input, Select, Textarea } from '@/ui/Input';
import { PageHeader } from '@/ui/PageHeader';
import { Skeleton } from '@/ui/Skeleton';
import { useToast } from '@/ui/toastContext';
import styles from './AdminPage.module.css';

type RoleFilter = 'all' | 'admin' | 'user';
type StatusFilter = 'all' | 'active' | 'banned';

function formatUsd(value: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 4,
    maximumFractionDigits: 6,
  }).format(value);
}

const COST_REPORT_DAYS = 30;

export default function AdminPage() {
  const { user: currentUser } = useAuth();
  const { showToast } = useToast();
  useDocumentTitle('Admin');

  const [actionError, setActionError] = useState<string | null>(null);
  const [busyEmail, setBusyEmail] = useState<string | null>(null);

  const [query, setQuery] = useState('');
  const [roleFilter, setRoleFilter] = useState<RoleFilter>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');

  const [ledgerEmail, setLedgerEmail] = useState<string | null>(null);
  const [ledger, setLedger] = useState<CreditTransaction[]>([]);
  const [isLedgerLoading, setIsLedgerLoading] = useState(false);
  const ledgerEmailRef = useRef<string | null>(null);
  const ledgerRequest = useRef(0);

  const [creditTarget, setCreditTarget] = useState<User | null>(null);
  const [creditDelta, setCreditDelta] = useState('');
  const [creditReason, setCreditReason] = useState<AdminCreditReason>('admin_grant');
  const [creditNote, setCreditNote] = useState('');
  const [creditError, setCreditError] = useState<string | null>(null);
  const [isApplying, setIsApplying] = useState(false);

  const [banTarget, setBanTarget] = useState<User | null>(null);

  const usersQuery = useQuery<User[]>({
    key: queryKeys.adminUsers(),
    fetcher: ({ signal }) => adminAPI.listUsers({ signal }),
    fallbackMessage: "We couldn't load the account list.",
  });

  const costsQuery = useQuery<AiCostReport>({
    key: queryKeys.adminCosts(COST_REPORT_DAYS),
    fetcher: ({ signal }) => adminAPI.getAiCostReport(COST_REPORT_DAYS, { signal }),
    fallbackMessage: "We couldn't load provider costs.",
  });

  const users = useMemo(() => usersQuery.data ?? [], [usersQuery.data]);
  const isLoading = usersQuery.status === 'pending' || usersQuery.status === 'idle';
  const loadError = usersQuery.error?.message ?? null;
  const costReport = costsQuery.data ?? null;
  const isCostLoading = costsQuery.status === 'pending' || costsQuery.status === 'idle';
  const costError = costsQuery.error?.message ?? null;
  const load = usersQuery.refetch;
  const loadCosts = costsQuery.refetch;

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return users.filter((account) => {
      if (needle && !`${account.name} ${account.email}`.toLowerCase().includes(needle)) {
        return false;
      }
      if (roleFilter !== 'all' && account.role !== roleFilter) {
        return false;
      }
      if (statusFilter === 'active' && account.is_banned) {
        return false;
      }
      if (statusFilter === 'banned' && !account.is_banned) {
        return false;
      }
      return true;
    });
  }, [users, query, roleFilter, statusFilter]);

  function replaceUser(updated: User) {
    queryCache.setData<User[]>(queryKeys.adminUsers(), (previous) =>
      previous?.map((account) => (account.email === updated.email ? updated : account)),
    );
  }

  async function toggleBan(account: User) {
    setActionError(null);
    setBusyEmail(account.email);
    try {
      replaceUser(await adminAPI.banUser(account.email, !account.is_banned));
      showToast({
        tone: 'success',
        title: account.is_banned ? 'Account unbanned' : 'Account banned',
        message: account.name,
      });
    } catch (caught) {
      setActionError(describeError(caught, "That account couldn't be updated.").message);
    } finally {
      setBusyEmail(null);
      setBanTarget(null);
    }
  }

  async function changeRole(account: User, role: 'admin' | 'user') {
    setActionError(null);
    setBusyEmail(account.email);
    try {
      replaceUser(await adminAPI.changeUserRole(account.email, role));
      showToast({ tone: 'success', title: `Role set to ${role}`, message: account.name });
    } catch (caught) {
      setActionError(describeError(caught, "That role couldn't be changed.").message);
    } finally {
      setBusyEmail(null);
    }
  }

  async function toggleLedger(account: User) {
    if (ledgerEmail === account.email) {
      ledgerRequest.current += 1;
      ledgerEmailRef.current = null;
      setLedgerEmail(null);
      setIsLedgerLoading(false);
      return;
    }
    const request = ledgerRequest.current + 1;
    ledgerRequest.current = request;
    ledgerEmailRef.current = account.email;
    setLedgerEmail(account.email);
    setLedger([]);
    setIsLedgerLoading(true);
    try {
      const transactions = await adminAPI.listUserCreditTransactions(account.email);
      if (ledgerRequest.current === request) {
        setLedger(transactions);
      }
    } catch (caught) {
      if (ledgerRequest.current === request) {
        setActionError(describeError(caught, "That ledger couldn't be loaded.").message);
      }
    } finally {
      if (ledgerRequest.current === request) {
        setIsLedgerLoading(false);
      }
    }
  }

  function openCreditDialog(account: User) {
    setCreditTarget(account);
    setCreditDelta('');
    setCreditReason('admin_grant');
    setCreditNote('');
    setCreditError(null);
  }

  const parsedDelta = Number(creditDelta);
  const deltaIsNumber = creditDelta.trim() !== '' && Number.isFinite(parsedDelta);

  async function applyCredits(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!creditTarget || isApplying) {
      return;
    }
    const target = creditTarget;
    setCreditError(null);

    if (!deltaIsNumber || parsedDelta === 0) {
      setCreditError('Enter a change other than zero.');
      return;
    }
    if (parsedDelta < 0 && POSITIVE_ONLY_ADMIN_REASONS.has(creditReason)) {
      setCreditError(
        `${reasonLabel(creditReason)} can only add credits. Use a correction to take them away.`,
      );
      return;
    }

    setIsApplying(true);
    try {
      const result = await adminAPI.changeCredits(
        target.email,
        parsedDelta,
        creditReason,
        creditNote.trim() || undefined,
      );
      replaceUser(result.user);
      if (ledgerEmailRef.current === target.email) {
        ledgerRequest.current += 1;
        setIsLedgerLoading(false);
        setLedger((current) =>
          current.some((entry) => entry.id === result.transaction.id)
            ? current
            : [result.transaction, ...current],
        );
      }
      setCreditTarget(null);
      showToast({
        tone: 'success',
        title: `Balance now ${result.transaction.balance_after}`,
        message: target.name,
      });
    } catch (caught) {
      setCreditError(describeError(caught, "That change couldn't be applied.").message);
    } finally {
      setIsApplying(false);
    }
  }

  const projected =
    creditTarget && deltaIsNumber && creditTarget.credits != null
      ? creditTarget.credits + parsedDelta
      : null;

  return (
    <div className={styles.page}>
      <PageHeader
        crumbs={[{ label: 'Admin' }]}
        badges={<Badge tone="accent">Administrator</Badge>}
        actions={<span className="tabular">{users.length} accounts</span>}
      />

      <div className={styles.body}>
        <h1 className={styles.title}>Accounts</h1>
        <p className={styles.subtitle}>
          Credit changes are permanent ledger entries. A mistake is corrected by another entry,
          never by editing a balance.
        </p>

        <section className={styles.costSection} aria-labelledby="provider-cost-title">
          <div className={styles.costHeading}>
            <div>
              <h2 id="provider-cost-title" className={styles.sectionTitle}>
                AI provider usage
              </h2>
              <p className={styles.subtitle}>
                Successful generations over the last 30 UTC days. Estimates use the rate version
                saved with each event and are not provider invoices.
              </p>
            </div>
            <Badge tone="neutral">UTC</Badge>
          </div>

          {isCostLoading ? (
            <Skeleton variant="block" />
          ) : costError ? (
            <ErrorState onRetry={() => void loadCosts()}>{costError}</ErrorState>
          ) : costReport ? (
            <>
              <div className={styles.costMetrics}>
                <div className={styles.costMetric}>
                  <span>Estimated cost</span>
                  <strong>{formatUsd(costReport.totals.estimated_cost_usd)}</strong>
                </div>
                <div className={styles.costMetric}>
                  <span>Successful generations</span>
                  <strong>{costReport.totals.successful_generations}</strong>
                </div>
                <div className={styles.costMetric}>
                  <span>Unpriced generations</span>
                  <strong>{costReport.totals.unpriced_generations}</strong>
                </div>
              </div>

              {costReport.daily.length > 0 ? (
                <div
                  className={styles.costTableWrap}
                  role="region"
                  aria-label="Provider costs by day"
                  tabIndex={0}
                >
                  <table className={styles.costTable}>
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>Provider / model</th>
                        <th>Rate version</th>
                        <th className={styles.numeric}>Generations</th>
                        <th className={styles.numeric}>Tokens</th>
                        <th className={styles.numeric}>Estimated USD</th>
                      </tr>
                    </thead>
                    <tbody>
                      {costReport.daily.map((row) => (
                        <tr
                          key={`${row.date}-${row.provider}-${row.model}-${row.pricing_version ?? 'unpriced'}`}
                        >
                          <td>{row.date}</td>
                          <td>
                            {row.provider} / {row.model}
                          </td>
                          <td>{row.pricing_version ?? 'Unpriced'}</td>
                          <td className={styles.numeric}>{row.successful_generations}</td>
                          <td className={styles.numeric}>
                            {row.prompt_tokens + row.completion_tokens}
                          </td>
                          <td className={styles.numeric}>
                            {row.pricing_version ? formatUsd(row.estimated_cost_usd) : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className={styles.accountEmail}>No successful generations in this period.</p>
              )}
            </>
          ) : null}
        </section>

        {loadError ? (
          <ErrorState className={styles.spaced} onRetry={() => void load()}>
            {loadError}
          </ErrorState>
        ) : null}

        {actionError ? (
          <Alert tone="destructive" live="alert" className={styles.spaced}>
            {actionError}
          </Alert>
        ) : null}

        <div className={styles.filters}>
          <Input
            label="Search accounts"
            hideLabel
            type="search"
            fieldClassName={styles.search}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search by name or email"
          />
          <Select
            label="Role"
            hideLabel
            fieldClassName={styles.filter}
            value={roleFilter}
            onChange={(event) => setRoleFilter(event.target.value as RoleFilter)}
          >
            <option value="all">All roles</option>
            <option value="admin">Admins</option>
            <option value="user">Students</option>
          </Select>
          <Select
            label="Status"
            hideLabel
            fieldClassName={styles.filter}
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}
          >
            <option value="all">All accounts</option>
            <option value="active">Active</option>
            <option value="banned">Banned</option>
          </Select>
        </div>

        {isLoading ? (
          <div className={styles.spaced} aria-hidden="true">
            <Skeleton variant="block" />
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            title="No accounts match"
            description="Try a different name, email, role or status."
          />
        ) : (
          <div
            className={styles.tableWrap}
            role="region"
            aria-label="Accounts"
            tabIndex={0}
          >
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th className={styles.numeric}>Credits</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {filtered.map((account) => {
                  const isSelf = account.email === currentUser?.email;
                  const busy = busyEmail === account.email;

                  return [
                    <tr key={account.email} className={account.is_banned ? styles.banned : undefined}>
                      <td className={styles.account}>
                        <div className={styles.accountName}>{account.name}</div>
                        <div className={styles.accountEmail}>{account.email}</div>
                      </td>
                      <td>
                        <Badge tone={account.role === 'admin' ? 'accent' : 'neutral'}>
                          {account.role === 'admin' ? 'Admin' : 'Student'}
                        </Badge>
                      </td>
                      <td>
                        <Badge tone={account.is_banned ? 'destructive' : 'success'}>
                          {account.is_banned ? 'Banned' : 'Active'}
                        </Badge>
                      </td>
                      <td className={styles.numeric}>
                        {account.credits == null ? (
                          <span className={styles.accountEmail}>Unmetered</span>
                        ) : (
                          account.credits
                        )}
                      </td>
                      <td>
                        <div className={styles.rowActions}>
                          <Button size="sm" variant="ghost" onClick={() => void toggleLedger(account)}>
                            Ledger
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={account.credits == null}
                            onClick={() => openCreditDialog(account)}
                          >
                            Credits
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={isSelf || busy}
                            onClick={() =>
                              void changeRole(account, account.role === 'admin' ? 'user' : 'admin')
                            }
                          >
                            {account.role === 'admin' ? 'Demote' : 'Promote'}
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={isSelf || busy}
                            onClick={() => setBanTarget(account)}
                          >
                            {account.is_banned ? 'Unban' : 'Ban'}
                          </Button>
                        </div>
                      </td>
                    </tr>,
                    ledgerEmail === account.email ? (
                      <tr key={`${account.email}-ledger`} className={styles.ledgerRow}>
                        <td colSpan={5}>
                          <div className={styles.ledger}>
                            {isLedgerLoading ? (
                              <Skeleton />
                            ) : ledger.length === 0 ? (
                              <p className={styles.accountEmail}>No credit activity recorded.</p>
                            ) : (
                              <table className={styles.ledgerTable}>
                                <tbody>
                                  {ledger.map((entry) => (
                                    <tr key={entry.id}>
                                      <td>{transactionLabel(entry)}</td>
                                      <td className={styles.numeric}>{formatDelta(entry.delta)}</td>
                                      <td className={styles.numeric}>{entry.balance_after}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            )}
                          </div>
                        </td>
                      </tr>
                    ) : null,
                  ];
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Dialog
        open={creditTarget !== null}
        onClose={() => setCreditTarget(null)}
        title="Adjust credits"
        size="sm"
        spreadFooter
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreditTarget(null)}>
              Cancel
            </Button>
            <Button
              type="submit"
              form="admin-credit-form"
              variant="primary"
              isLoading={isApplying}
              loadingLabel="Applying"
            >
              Apply
            </Button>
          </>
        }
      >
        {creditTarget ? (
          <>
            <p className={styles.dialogMeta}>
              <span>{creditTarget.email}</span>
              {' · '}
              <span>Current balance: {creditTarget.credits}</span>
            </p>

            <form id="admin-credit-form" className={styles.formGrid} onSubmit={applyCredits}>
              {creditError ? (
                <Alert tone="destructive" live="alert">
                  {creditError}
                </Alert>
              ) : null}

              <Input
                label="Credit change"
                value={creditDelta}
                onChange={(event) => setCreditDelta(event.target.value)}
                placeholder="e.g. 10 or -5"
                hint="A signed number. Negative values take credits away."
                inputMode="numeric"
              />

              <Select
                label="Reason"
                value={creditReason}
                onChange={(event) => setCreditReason(event.target.value as AdminCreditReason)}
              >
                {ADMIN_CREDIT_REASONS.map((reason) => (
                  <option key={reason} value={reason}>
                    {reasonLabel(reason)}
                  </option>
                ))}
              </Select>

              <Textarea
                label="Note"
                optional
                rows={2}
                value={creditNote}
                onChange={(event) => setCreditNote(event.target.value)}
                placeholder="Support ticket reference, for example"
              />

              <p className={styles.preview}>
                {projected != null ? (
                  <>
                    New balance will be <strong>{projected}</strong>.{' '}
                  </>
                ) : null}
                Every change is recorded permanently and can only be corrected by another change.
              </p>
            </form>
          </>
        ) : null}
      </Dialog>

      <ConfirmDialog
        open={banTarget !== null}
        onClose={() => setBanTarget(null)}
        onConfirm={() => banTarget && void toggleBan(banTarget)}
        title={banTarget?.is_banned ? 'Unban this account?' : 'Ban this account?'}
        description={
          banTarget?.is_banned
            ? `${banTarget.email} will be able to sign in again immediately.`
            : `${banTarget?.email} will be signed out of every request immediately, including sessions that are already open.`
        }
        confirmLabel={banTarget?.is_banned ? 'Unban' : 'Ban'}
        destructive={!banTarget?.is_banned}
        isPending={busyEmail === banTarget?.email}
      />
    </div>
  );
}
