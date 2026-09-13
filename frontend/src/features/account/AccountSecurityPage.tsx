import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { userAPI } from '@/api/user';
import { describeError } from '@/api/errors';
import { useAuth } from '@/context/AuthContext';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { ConfirmDialog } from '@/ui/ConfirmDialog';
import { PasswordInput } from '@/ui/PasswordInput';
import styles from './AccountPage.module.css';

export default function AccountSecurityPage() {
  const navigate = useNavigate();
  const { logout } = useAuth();
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  
  const [status, setStatus] = useState<'idle' | 'submitting' | 'success'>('idle');
  const [error, setError] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deletePassword, setDeletePassword] = useState('');
  const [deletePending, setDeletePending] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (status === 'submitting') {
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("New passwords don't match.");
      return;
    }

    setError(null);
    setStatus('submitting');

    try {
      await userAPI.changePassword(currentPassword, newPassword);
      setStatus('success');
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (caught) {
      setError(describeError(caught, 'Failed to change password.').message);
      setStatus('idle');
    }
  }

  async function handleDeleteAccount() {
    if (deletePending) {
      return;
    }
    if (!deletePassword.trim()) {
      setDeleteError('Please enter your current password.');
      return;
    }
    setDeletePending(true);
    setDeleteError(null);
    try {
      await userAPI.deleteAccount(deletePassword);
      localStorage.removeItem('lumina_ad_consent');
      await logout({ remote: false });
      navigate('/login', { replace: true });
    } catch (caught) {
      setDeleteError(describeError(caught, 'Account deletion could not be requested.').message);
      setDeletePending(false);
    }
  }

  return (
    <>
      <section className={styles.section}>
        <h2 className={styles.sectionHeading}>Security</h2>
        <p className={styles.sectionLede}>Change your password to keep your account secure.</p>

        <form className={styles.formGrid} onSubmit={handleSubmit}>
        {error ? (
          <Alert tone="destructive" live="alert">
            {error}
          </Alert>
        ) : null}

        {status === 'success' ? (
          <Alert tone="success" live="status">
            Your password has been changed successfully.
          </Alert>
        ) : null}

        <PasswordInput
          label="Current password"
          autoComplete="current-password"
          required
          value={currentPassword}
          onChange={(event) => {
            setCurrentPassword(event.target.value);
            setStatus('idle');
          }}
          disabled={status === 'submitting'}
        />

        <PasswordInput
          label="New password"
          autoComplete="new-password"
          required
          value={newPassword}
          onChange={(event) => {
            setNewPassword(event.target.value);
            setStatus('idle');
          }}
          disabled={status === 'submitting'}
        />

        <PasswordInput
          label="Confirm new password"
          autoComplete="new-password"
          required
          value={confirmPassword}
          onChange={(event) => {
            setConfirmPassword(event.target.value);
            setStatus('idle');
          }}
          disabled={status === 'submitting'}
        />

        <div className={styles.actions}>
          <Button
            type="submit"
            variant="primary"
            isLoading={status === 'submitting'}
            loadingLabel="Saving"
          >
            Change password
          </Button>
        </div>
        </form>
      </section>

      <section className={styles.dangerZone} aria-labelledby="delete-account-heading">
        <div>
          <h2 className={styles.dangerHeading} id="delete-account-heading">Delete account</h2>
          <p className={styles.dangerCopy}>
            Permanently remove your courses, uploads, generated study data, profile knowledge,
            activity, and account credentials. This cannot be undone.
          </p>
        </div>
        <Button variant="destructive" onClick={() => setDeleteOpen(true)}>
          Delete my account
        </Button>
      </section>

      <ConfirmDialog
        open={deleteOpen}
        onClose={() => {
          if (!deletePending) {
            setDeleteOpen(false);
            setDeletePassword('');
            setDeleteError(null);
          }
        }}
        onConfirm={() => void handleDeleteAccount()}
        title="Permanently delete your account?"
        description="Your account will be locked immediately while permanent cleanup runs."
        confirmLabel="Delete account permanently"
        pendingLabel="Requesting deletion"
        isPending={deletePending}
        canConfirm={Boolean(deletePassword.trim())}
        confirmPhrase="DELETE"
        confirmPhraseLabel="Type DELETE to confirm"
      >
        <div className={styles.deleteDialogBody}>
          <p>
            Active database records, stored source files, and search vectors are removed by a
            retrying cleanup process. If cleanup fails, your account stays locked until it succeeds.
          </p>
          <p>
            Privacy-safe operational records and backups are retained for bounded periods and are
            not erased immediately. Hosted noncurrent object versions can remain for up to 90 days;
            self-hosted backup retention is controlled by the operator. See the{' '}
            <Link to="/#privacy" target="_blank" rel="noopener noreferrer">
              Privacy Notice
            </Link>.
          </p>
          {deleteError ? (
            <Alert tone="destructive" live="alert">{deleteError}</Alert>
          ) : null}
          <PasswordInput
            label="Current password for account deletion"
            autoComplete="current-password"
            required
            value={deletePassword}
            onChange={(event) => setDeletePassword(event.target.value)}
            disabled={deletePending}
          />
        </div>
      </ConfirmDialog>
    </>
  );
}
