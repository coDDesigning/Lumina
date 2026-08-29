import { useState } from 'react';
import type { FormEvent } from 'react';
import { userAPI } from '@/api/user';
import { describeError } from '@/api/errors';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { Input } from '@/ui/Input';
import styles from './AccountPage.module.css';

export default function AccountSecurityPage() {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  
  const [status, setStatus] = useState<'idle' | 'submitting' | 'success'>('idle');
  const [error, setError] = useState<string | null>(null);

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

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionHeading}>Security</h2>
      <p className={styles.sectionLede}>Change your password to keep your account secure.</p>
      
      <form className={styles.form} onSubmit={handleSubmit}>
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

        <Input
          label="Current password"
          type="password"
          autoComplete="current-password"
          required
          value={currentPassword}
          onChange={(event) => {
            setCurrentPassword(event.target.value);
            setStatus('idle');
          }}
          disabled={status === 'submitting'}
        />

        <Input
          label="New password"
          type="password"
          autoComplete="new-password"
          required
          value={newPassword}
          onChange={(event) => {
            setNewPassword(event.target.value);
            setStatus('idle');
          }}
          disabled={status === 'submitting'}
        />

        <Input
          label="Confirm new password"
          type="password"
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
  );
}
