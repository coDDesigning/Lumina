import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { authAPI } from '@/api/auth';
import { describeError } from '@/api/errors';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { Input } from '@/ui/Input';
import { AuthLayout } from './AuthLayout';
import styles from './AuthLayout.module.css';

export default function ResetPasswordPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token');

  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [status, setStatus] = useState<'idle' | 'submitting' | 'success'>('idle');
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  if (!token) {
    return (
      <AuthLayout
        tone={0}
        documentTitle="Reset password"
        title="Invalid link."
        subtitle="This password reset link is missing or broken."
        footer={
          <>
            <Link to="/forgot-password">Request a new link</Link>
          </>
        }
      >
        <div className={styles.form} />
      </AuthLayout>
    );
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (status === 'submitting') {
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords don't match. Please try again.");
      return;
    }
    setError(null);
    setStatus('submitting');

    try {
      const response = await authAPI.confirmPasswordReset(token!, password);
      setStatus('success');
      setMessage(response.message);
    } catch (caught) {
      setError(describeError(caught, 'Failed to reset password.').message);
      setStatus('idle');
    }
  }

  return (
    <AuthLayout
      tone={0}
      documentTitle="Choose new password"
      title="Choose a new password."
      subtitle="Make sure it's at least 8 characters long."
      footer={
        status === 'success' ? (
          <>
            <Link to="/login">Sign in</Link>
          </>
        ) : null
      }
    >
      {status === 'success' && message ? (
        <div className={styles.form}>
          <Alert tone="success" live="status">
            {message}
          </Alert>
        </div>
      ) : (
        <form className={styles.form} onSubmit={handleSubmit}>
          {error ? (
            <Alert tone="destructive" live="alert">
              {error}
            </Alert>
          ) : null}

          <Input
            label="New password"
            type="password"
            autoComplete="new-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={status === 'submitting'}
          />

          <Input
            label="Confirm password"
            type="password"
            autoComplete="new-password"
            required
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
            disabled={status === 'submitting'}
          />

          <Button
            type="submit"
            variant="primary"
            size="lg"
            fullWidth
            className={styles.submit}
            isLoading={status === 'submitting'}
            loadingLabel="Saving"
          >
            Reset password
          </Button>
        </form>
      )}
    </AuthLayout>
  );
}
