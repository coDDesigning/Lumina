import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Lock,
  Coins,
  Search,
  Shield,
  ShieldAlert,
  ShieldCheck,
  UserCheck,
  UserX,
  Users,
} from 'lucide-react';
import { adminAPI } from '../api/admin';
import {
  ADMIN_CREDIT_REASONS,
  POSITIVE_ONLY_ADMIN_REASONS,
  formatDelta,
  reasonLabel,
  transactionLabel,
} from '../api/creditLabels';
import { describeError } from '../api/errors';
import type { AdminCreditReason, CreditTransaction, User } from '../api/types';
import { LoadingSpinner } from '../components/LoadingSpinner';
import PageLayout from '../components/PageLayout';
import { useAuth } from '../context/AuthContext';
import './pages.css';

export function AdminPage() {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [roleFilter, setRoleFilter] = useState<'all' | 'admin' | 'user'>('all');
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'banned'>('all');
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);
  const [processingEmail, setProcessingEmail] = useState<string | null>(null);
  const [ledgerEmail, setLedgerEmail] = useState<string | null>(null);
  const [ledger, setLedger] = useState<CreditTransaction[]>([]);
  const [creditTarget, setCreditTarget] = useState<User | null>(null);
  const [creditDelta, setCreditDelta] = useState('');
  const [creditReason, setCreditReason] =
    useState<AdminCreditReason>('admin_grant');
  const [creditNote, setCreditNote] = useState('');
  const [creditError, setCreditError] = useState<string | null>(null);
  const [creditSubmitting, setCreditSubmitting] = useState(false);
  const [ledgerLoading, setLedgerLoading] = useState(false);

  const fetchUsers = useCallback(async () => {
    setIsLoading(true);
    setActionError(null);
    try {
      const data = await adminAPI.listUsers();
      setUsers(data);
    } catch (err) {
      setActionError(describeError(err, 'Failed to load user list.').message);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  const handleToggleBan = async (targetUser: User) => {
    const nextBanStatus = !targetUser.is_banned;
    const actionName = nextBanStatus ? 'ban' : 'unban';

    if (
      !window.confirm(
        `Are you sure you want to ${actionName} user "${targetUser.email}"?`,
      )
    ) {
      return;
    }

    setProcessingEmail(targetUser.email);
    setActionError(null);
    setActionSuccess(null);

    try {
      const updated = await adminAPI.banUser(targetUser.email, nextBanStatus);
      setUsers((prev) =>
        prev.map((u) => (u.id === updated.id ? updated : u)),
      );
      setActionSuccess(
        `User "${targetUser.email}" was successfully ${nextBanStatus ? 'banned' : 'unbanned'}.`,
      );
    } catch (err) {
      setActionError(
        describeError(err, `Failed to ${actionName} user.`).message,
      );
    } finally {
      setProcessingEmail(null);
    }
  };

  const openLedger = async (targetUser: User) => {
    setLedgerEmail(targetUser.email);
    setLedgerLoading(true);
    try {
      setLedger(await adminAPI.listUserCreditTransactions(targetUser.email, 20));
    } catch (err) {
      setLedger([]);
      setActionError(describeError(err, 'Failed to load credit history.').message);
    } finally {
      setLedgerLoading(false);
    }
  };

  const applyCreditChange = async (
    targetUser: User,
    change: () => Promise<{ user: User }>,
    successMessage: string,
    onFailure?: (message: string) => void,
  ): Promise<boolean> => {
    setProcessingEmail(targetUser.email);
    setActionError(null);
    setActionSuccess(null);

    try {
      const { user: updated } = await change();
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
      setActionSuccess(successMessage);
      if (ledgerEmail === targetUser.email) {
        await openLedger(targetUser);
      }
      return true;
    } catch (err) {
      const { message } = describeError(err, 'Failed to change credits.');
      if (onFailure) {
        onFailure(message);
      } else {
        setActionError(message);
      }
      return false;
    } finally {
      setProcessingEmail(null);
    }
  };

  const openCreditModal = (targetUser: User) => {
    setCreditTarget(targetUser);
    setCreditDelta('');
    setCreditReason('admin_grant');
    setCreditNote('');
    setCreditError(null);
  };

  const closeCreditModal = () => {
    setCreditTarget(null);
    setCreditError(null);
  };

  useEffect(() => {
    if (!creditTarget) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeCreditModal();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [creditTarget]);

  const handleCreditSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!creditTarget) return;

    const delta = Number(creditDelta);
    if (creditDelta.trim() === '' || !Number.isFinite(delta) || delta === 0) {
      setCreditError('Enter a non-zero number of credits.');
      return;
    }
    if (POSITIVE_ONLY_ADMIN_REASONS.has(creditReason) && delta < 0) {
      setCreditError(
        'A grant must add credits. Choose Administrator adjustment to remove them.',
      );
      return;
    }

    setCreditSubmitting(true);
    setCreditError(null);
    try {
      const succeeded = await applyCreditChange(
        creditTarget,
        () =>
          adminAPI.changeCredits(
            creditTarget.email,
            delta,
            creditReason,
            creditNote.trim(),
          ),
        `Credits for "${creditTarget.email}" changed by ${formatDelta(delta)}.`,
        (message) => setCreditError(message),
      );
      if (succeeded) {
        setCreditTarget(null);
      }
    } finally {
      setCreditSubmitting(false);
    }
  };

  const handleToggleLedger = async (targetUser: User) => {
    if (ledgerEmail === targetUser.email) {
      setLedgerEmail(null);
      setLedger([]);
      return;
    }
    await openLedger(targetUser);
  };

  const handleToggleRole = async (targetUser: User) => {
    const nextRole = targetUser.role === 'admin' ? 'user' : 'admin';
    const actionName = nextRole === 'admin' ? 'promote to Admin' : 'demote to Standard User';

    if (
      !window.confirm(
        `Are you sure you want to ${actionName} for "${targetUser.email}"?`,
      )
    ) {
      return;
    }

    setProcessingEmail(targetUser.email);
    setActionError(null);
    setActionSuccess(null);

    try {
      const updated = await adminAPI.changeUserRole(targetUser.email, nextRole);
      setUsers((prev) =>
        prev.map((u) => (u.id === updated.id ? updated : u)),
      );
      setActionSuccess(
        `User "${targetUser.email}" role was successfully updated to ${nextRole.toUpperCase()}.`,
      );
    } catch (err) {
      setActionError(
        describeError(err, `Failed to change user role.`).message,
      );
    } finally {
      setProcessingEmail(null);
    }
  };

  const filteredUsers = useMemo(() => {
    return users.filter((u) => {
      const query = searchQuery.toLowerCase().trim();
      const matchesSearch =
        !query ||
        u.email.toLowerCase().includes(query) ||
        u.name.toLowerCase().includes(query);

      const matchesRole =
        roleFilter === 'all' || u.role.toLowerCase() === roleFilter;

      const matchesStatus =
        statusFilter === 'all' ||
        (statusFilter === 'banned' ? u.is_banned : !u.is_banned);

      return matchesSearch && matchesRole && matchesStatus;
    });
  }, [users, searchQuery, roleFilter, statusFilter]);

  if (currentUser?.role !== 'admin') {
    return (
      <PageLayout
        title="Admin Control Center"
        description="Administrator privileges are required to access this page."
        eyebrow="Access Control"
        icon={Lock}
      >
        <div style={{ textAlign: 'center', padding: '48px 16px' }}>
          <ShieldAlert size={56} color="#dc2626" style={{ margin: '0 auto 16px auto' }} />
          <h2 style={{ fontSize: '22px', color: '#1e1b4b', margin: '0 0 8px 0' }}>
            Access Denied
          </h2>
          <p style={{ color: '#6b7280', maxWidth: '420px', margin: '0 auto' }}>
            You do not have administrative permissions to view or manage platform accounts.
          </p>
        </div>
      </PageLayout>
    );
  }

  return (
    <PageLayout
      title="Admin Control Center"
      description="Manage registered users, assign roles, monitor credit allocations, and control access permissions."
      eyebrow="System Administration"
      icon={Shield}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        {/* Status Alerts */}
        {actionSuccess && (
          <div
            style={{
              padding: '12px 16px',
              background: '#ecfdf5',
              border: '1px solid #a7f3d0',
              borderRadius: '12px',
              color: '#065f46',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              fontSize: '14px',
            }}
            role="status"
          >
            <CheckCircle2 size={18} />
            <span>{actionSuccess}</span>
          </div>
        )}

        {actionError && (
          <div
            style={{
              padding: '12px 16px',
              background: '#fef2f2',
              border: '1px solid #fecaca',
              borderRadius: '12px',
              color: '#991b1b',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              fontSize: '14px',
            }}
            role="alert"
          >
            <AlertTriangle size={18} />
            <span>{actionError}</span>
          </div>
        )}

        {/* Filter / Search Controls */}
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: '12px',
            alignItems: 'center',
            justifyContent: 'space-between',
            background: '#ffffff',
            padding: '16px 20px',
            borderRadius: '14px',
            boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
            border: '1px solid #f1f5f9',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              background: '#f8fafc',
              border: '1px solid #e2e8f0',
              borderRadius: '10px',
              padding: '8px 14px',
              flex: '1',
              minWidth: '240px',
              maxWidth: '400px',
            }}
          >
            <Search size={18} color="#94a3b8" />
            <input
              type="text"
              placeholder="Search by name or email…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              style={{
                border: 'none',
                background: 'transparent',
                outline: 'none',
                width: '100%',
                fontSize: '14px',
              }}
            />
          </div>

          <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
            <select
              value={roleFilter}
              onChange={(e) => setRoleFilter(e.target.value as 'all' | 'admin' | 'user')}
              style={{
                padding: '8px 14px',
                borderRadius: '10px',
                border: '1px solid #e2e8f0',
                background: '#ffffff',
                fontSize: '14px',
                color: '#334155',
                cursor: 'pointer',
              }}
            >
              <option value="all">All Roles</option>
              <option value="admin">Admins Only</option>
              <option value="user">Standard Users Only</option>
            </select>

            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as 'all' | 'active' | 'banned')}
              style={{
                padding: '8px 14px',
                borderRadius: '10px',
                border: '1px solid #e2e8f0',
                background: '#ffffff',
                fontSize: '14px',
                color: '#334155',
                cursor: 'pointer',
              }}
            >
              <option value="all">All Statuses</option>
              <option value="active">Active Only</option>
              <option value="banned">Banned Only</option>
            </select>
          </div>
        </div>

        {/* User Table */}
        <div
          style={{
            background: '#ffffff',
            borderRadius: '16px',
            border: '1px solid #f1f5f9',
            boxShadow: '0 4px 16px rgba(0,0,0,0.03)',
            overflow: 'hidden',
          }}
        >
          {isLoading ? (
            <div style={{ padding: '48px', textAlign: 'center' }}>
              <LoadingSpinner size="md" />
              <p style={{ marginTop: '12px', color: '#64748b' }}>Loading user directory…</p>
            </div>
          ) : filteredUsers.length === 0 ? (
            <div style={{ padding: '48px', textAlign: 'center', color: '#64748b' }}>
              <Users size={40} style={{ margin: '0 auto 12px auto', opacity: 0.5 }} />
              <p style={{ margin: 0, fontWeight: 500 }}>No users found matching your filters.</p>
            </div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
                <thead>
                  <tr style={{ background: '#f8fafc', borderBottom: '1px solid #e2e8f0' }}>
                    <th style={{ padding: '14px 20px', fontSize: '12px', fontWeight: 600, color: '#64748b', textTransform: 'uppercase' }}>User</th>
                    <th style={{ padding: '14px 20px', fontSize: '12px', fontWeight: 600, color: '#64748b', textTransform: 'uppercase' }}>Role</th>
                    <th style={{ padding: '14px 20px', fontSize: '12px', fontWeight: 600, color: '#64748b', textTransform: 'uppercase' }}>Status</th>
                    <th style={{ padding: '14px 20px', fontSize: '12px', fontWeight: 600, color: '#64748b', textTransform: 'uppercase' }}>Credits</th>
                    <th style={{ padding: '14px 20px', fontSize: '12px', fontWeight: 600, color: '#64748b', textTransform: 'uppercase', textAlign: 'right' }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredUsers.map((u) => {
                    const isSelf = u.id === currentUser.id;
                    const isBusy = processingEmail === u.email;

                    return (
                      <tr
                        key={u.id}
                        style={{
                          borderBottom: '1px solid #f1f5f9',
                          transition: 'background 0.15s ease',
                          opacity: isBusy ? 0.6 : 1,
                        }}
                      >
                        <td style={{ padding: '16px 20px' }}>
                          <div style={{ fontWeight: 600, color: '#1e293b' }}>
                            {u.name || 'Unnamed User'}
                            {isSelf && (
                              <span
                                style={{
                                  marginLeft: '8px',
                                  fontSize: '11px',
                                  background: '#e0f2fe',
                                  color: '#0369a1',
                                  padding: '2px 8px',
                                  borderRadius: '999px',
                                  fontWeight: 500,
                                }}
                              >
                                You
                              </span>
                            )}
                          </div>
                          <div style={{ fontSize: '13px', color: '#64748b', marginTop: '2px' }}>{u.email}</div>
                        </td>

                        <td style={{ padding: '16px 20px' }}>
                          <span
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '5px',
                              padding: '4px 10px',
                              borderRadius: '999px',
                              fontSize: '12px',
                              fontWeight: 600,
                              background: u.role === 'admin' ? '#f5f3ff' : '#f1f5f9',
                              color: u.role === 'admin' ? '#7c3aed' : '#475569',
                              border: `1px solid ${u.role === 'admin' ? '#ddd6fe' : '#e2e8f0'}`,
                            }}
                          >
                            {u.role === 'admin' ? <ShieldCheck size={13} /> : null}
                            {u.role.toUpperCase()}
                          </span>
                        </td>

                        <td style={{ padding: '16px 20px' }}>
                          <span
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '5px',
                              padding: '4px 10px',
                              borderRadius: '999px',
                              fontSize: '12px',
                              fontWeight: 600,
                              background: u.is_banned ? '#fef2f2' : '#ecfdf5',
                              color: u.is_banned ? '#dc2626' : '#059669',
                              border: `1px solid ${u.is_banned ? '#fecaca' : '#a7f3d0'}`,
                            }}
                          >
                            {u.is_banned ? <UserX size={13} /> : <UserCheck size={13} />}
                            {u.is_banned ? 'Banned' : 'Active'}
                          </span>
                        </td>

                        <td style={{ padding: '16px 20px', fontSize: '14px', fontWeight: 600, color: '#334155' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <span>{u.credits === null ? '∞ Unlimited' : u.credits}</span>
                            {u.credits !== null && (
                              <button
                                type="button"
                                onClick={() => handleToggleLedger(u)}
                                disabled={isBusy}
                                style={{
                                  display: 'inline-flex',
                                  alignItems: 'center',
                                  gap: '4px',
                                  padding: '2px 8px',
                                  borderRadius: '999px',
                                  fontSize: '11px',
                                  fontWeight: 600,
                                  border: '1px solid #e2e8f0',
                                  background: ledgerEmail === u.email ? '#eef2ff' : '#ffffff',
                                  color: '#475569',
                                  cursor: isBusy ? 'not-allowed' : 'pointer',
                                }}
                                title="Show why this balance is what it is"
                              >
                                <Coins size={11} />
                                History
                              </button>
                            )}
                          </div>
                        </td>

                        <td style={{ padding: '16px 20px', textAlign: 'right' }}>
                          <div style={{ display: 'inline-flex', gap: '8px' }}>
                            <button
                              type="button"
                              onClick={() => handleToggleRole(u)}
                              disabled={isSelf || isBusy}
                              style={{
                                padding: '6px 12px',
                                borderRadius: '8px',
                                fontSize: '12px',
                                fontWeight: 600,
                                border: '1px solid #e2e8f0',
                                background: '#ffffff',
                                color: '#334155',
                                cursor: isSelf || isBusy ? 'not-allowed' : 'pointer',
                                opacity: isSelf ? 0.5 : 1,
                              }}
                              title={isSelf ? 'You cannot alter your own admin role' : undefined}
                            >
                              {u.role === 'admin' ? 'Demote' : 'Make Admin'}
                            </button>

                            <button
                              type="button"
                              onClick={() => handleToggleBan(u)}
                              disabled={isSelf || isBusy}
                              style={{
                                padding: '6px 12px',
                                borderRadius: '8px',
                                fontSize: '12px',
                                fontWeight: 600,
                                border: `1px solid ${u.is_banned ? '#a7f3d0' : '#fecaca'}`,
                                background: u.is_banned ? '#ecfdf5' : '#fef2f2',
                                color: u.is_banned ? '#065f46' : '#991b1b',
                                cursor: isSelf || isBusy ? 'not-allowed' : 'pointer',
                                opacity: isSelf ? 0.5 : 1,
                              }}
                              title={isSelf ? 'You cannot ban your own account' : undefined}
                            >
                              {u.is_banned ? 'Unban' : 'Ban'}
                            </button>

                            <button
                              type="button"
                              onClick={() => openCreditModal(u)}
                              disabled={isBusy || u.credits === null}
                              style={{
                                padding: '6px 12px',
                                borderRadius: '8px',
                                fontSize: '12px',
                                fontWeight: 600,
                                border: '1px solid #c7d2fe',
                                background: '#eef2ff',
                                color: '#3730a3',
                                cursor:
                                  isBusy || u.credits === null ? 'not-allowed' : 'pointer',
                                opacity: u.credits === null ? 0.5 : 1,
                              }}
                              title={
                                u.credits === null
                                  ? 'This account is not metered and holds no balance'
                                  : undefined
                              }
                            >
                              Credits
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {ledgerEmail && (
            <section
              style={{
                marginTop: '20px',
                border: '1px solid #e2e8f0',
                borderRadius: '12px',
                overflow: 'hidden',
              }}
            >
              <header
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  padding: '12px 20px',
                  background: '#f8fafc',
                  borderBottom: '1px solid #e2e8f0',
                }}
              >
                <Coins size={15} style={{ color: '#6366f1' }} />
                <strong style={{ fontSize: '14px', color: '#1e293b' }}>
                  Credit history for {ledgerEmail}
                </strong>
              </header>

              {ledgerLoading ? (
                <p style={{ margin: 0, padding: '16px 20px', color: '#64748b', fontSize: '13px' }}>
                  Loading credit history...
                </p>
              ) : ledger.length === 0 ? (
                <p style={{ margin: 0, padding: '16px 20px', color: '#64748b', fontSize: '13px' }}>
                  No credit transactions recorded for this account.
                </p>
              ) : (
                <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
                  <thead>
                    <tr style={{ background: '#ffffff', borderBottom: '1px solid #e2e8f0' }}>
                      <th style={{ padding: '10px 20px', fontSize: '11px', fontWeight: 600, color: '#64748b', textTransform: 'uppercase' }}>When</th>
                      <th style={{ padding: '10px 20px', fontSize: '11px', fontWeight: 600, color: '#64748b', textTransform: 'uppercase' }}>Reason</th>
                      <th style={{ padding: '10px 20px', fontSize: '11px', fontWeight: 600, color: '#64748b', textTransform: 'uppercase' }}>By</th>
                      <th style={{ padding: '10px 20px', fontSize: '11px', fontWeight: 600, color: '#64748b', textTransform: 'uppercase', textAlign: 'right' }}>Change</th>
                      <th style={{ padding: '10px 20px', fontSize: '11px', fontWeight: 600, color: '#64748b', textTransform: 'uppercase', textAlign: 'right' }}>Balance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ledger.map((entry) => (
                      <tr key={entry.id} style={{ borderBottom: '1px solid #f1f5f9' }}>
                        <td style={{ padding: '10px 20px', fontSize: '13px', color: '#64748b', whiteSpace: 'nowrap' }}>
                          {new Date(entry.created_at).toLocaleString()}
                        </td>
                        <td style={{ padding: '10px 20px', fontSize: '13px', color: '#1e293b' }}>
                          {transactionLabel(entry)}
                          {entry.note ? (
                            <span style={{ color: '#94a3b8' }}> &middot; {entry.note}</span>
                          ) : null}
                        </td>
                        <td style={{ padding: '10px 20px', fontSize: '13px', color: '#64748b' }}>
                          {entry.actor_label ?? entry.actor_type}
                        </td>
                        <td
                          style={{
                            padding: '10px 20px',
                            fontSize: '13px',
                            fontWeight: 700,
                            textAlign: 'right',
                            color: entry.delta >= 0 ? '#059669' : '#dc2626',
                          }}
                        >
                          {formatDelta(entry.delta)}
                        </td>
                        <td style={{ padding: '10px 20px', fontSize: '13px', color: '#334155', textAlign: 'right' }}>
                          {entry.balance_after}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          )}
        </div>
      </div>

      {creditTarget && (
        <div
          role="presentation"
          onClick={closeCreditModal}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(15, 23, 42, 0.45)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '16px',
            zIndex: 50,
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="credit-modal-title"
            onClick={(event) => event.stopPropagation()}
            style={{
              width: 'min(440px, 100%)',
              background: '#ffffff',
              borderRadius: '16px',
              padding: '24px',
              boxShadow: '0 20px 50px rgba(15, 23, 42, 0.25)',
            }}
          >
            <h2
              id="credit-modal-title"
              style={{ margin: 0, fontSize: '18px', color: '#0f172a' }}
            >
              Change credits
            </h2>
            <p style={{ margin: '6px 0 0', fontSize: '13px', color: '#475569' }}>
              {creditTarget.email}
            </p>
            <p style={{ margin: '2px 0 18px', fontSize: '13px', color: '#475569' }}>
              Current balance: {creditTarget.credits}
            </p>

            <form onSubmit={handleCreditSubmit}>
              <label
                htmlFor="credit-delta"
                style={{
                  display: 'block',
                  fontSize: '13px',
                  fontWeight: 600,
                  color: '#334155',
                  marginBottom: '6px',
                }}
              >
                Credit change &mdash; a negative number removes credits
              </label>
              <input
                id="credit-delta"
                type="number"
                step="any"
                inputMode="decimal"
                autoFocus
                value={creditDelta}
                onChange={(event) => setCreditDelta(event.target.value)}
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  borderRadius: '8px',
                  border: '1px solid #cbd5e1',
                  fontSize: '14px',
                  marginBottom: '14px',
                }}
              />

              <label
                htmlFor="credit-reason"
                style={{
                  display: 'block',
                  fontSize: '13px',
                  fontWeight: 600,
                  color: '#334155',
                  marginBottom: '6px',
                }}
              >
                Reason
              </label>
              <select
                id="credit-reason"
                value={creditReason}
                onChange={(event) =>
                  setCreditReason(event.target.value as AdminCreditReason)
                }
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  borderRadius: '8px',
                  border: '1px solid #cbd5e1',
                  fontSize: '14px',
                  marginBottom: '14px',
                  background: '#ffffff',
                }}
              >
                {ADMIN_CREDIT_REASONS.map((reason) => (
                  <option key={reason} value={reason}>
                    {reasonLabel(reason)}
                  </option>
                ))}
              </select>

              <label
                htmlFor="credit-note"
                style={{
                  display: 'block',
                  fontSize: '13px',
                  fontWeight: 600,
                  color: '#334155',
                  marginBottom: '6px',
                }}
              >
                Note (optional)
              </label>
              <textarea
                id="credit-note"
                rows={3}
                maxLength={500}
                value={creditNote}
                onChange={(event) => setCreditNote(event.target.value)}
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  borderRadius: '8px',
                  border: '1px solid #cbd5e1',
                  fontSize: '14px',
                  resize: 'vertical',
                }}
              />

              {creditError && (
                <p
                  role="alert"
                  style={{
                    margin: '12px 0 0',
                    fontSize: '13px',
                    color: '#b91c1c',
                  }}
                >
                  {creditError}
                </p>
              )}

              <div
                style={{
                  display: 'flex',
                  justifyContent: 'flex-end',
                  gap: '8px',
                  marginTop: '18px',
                }}
              >
                <button
                  type="button"
                  onClick={closeCreditModal}
                  style={{
                    padding: '8px 16px',
                    borderRadius: '8px',
                    fontSize: '13px',
                    fontWeight: 600,
                    border: '1px solid #e2e8f0',
                    background: '#ffffff',
                    color: '#334155',
                    cursor: 'pointer',
                  }}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={creditSubmitting}
                  style={{
                    padding: '8px 16px',
                    borderRadius: '8px',
                    fontSize: '13px',
                    fontWeight: 600,
                    border: '1px solid #4f46e5',
                    background: '#4f46e5',
                    color: '#ffffff',
                    cursor: creditSubmitting ? 'not-allowed' : 'pointer',
                    opacity: creditSubmitting ? 0.6 : 1,
                  }}
                >
                  Apply
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </PageLayout>
  );
}

export default AdminPage;
