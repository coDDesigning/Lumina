import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { authAPI } from '@/api/auth';
import { describeError } from '@/api/errors';
import { queryKeys } from '@/api/queryKeys';
import { useQuery } from '@/lib/query/useQuery';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { PasswordInput } from '@/ui/PasswordInput';
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
  // Read rather than assumed: the minimum is configurable, and a screen that
  // stated a different one from the server would be telling people the wrong
  // rule. See docs/authentication.md.
  const { data: passwordPolicy } = useQuery({
    key: queryKeys.passwordPolicy(),
    fetcher: ({ signal }) => authAPI.getPasswordPolicy({ signal }),
    fallbackMessage: 'Could not load the password policy.',
    staleTime: Infinity,
  });

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
      subtitle={passwordPolicy?.description}
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

          <PasswordInput
            label="New password"
            autoComplete="new-password"
            required
            minLength={passwordPolicy?.minimum_length}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={status === 'submitting'}
          />

          <PasswordInput
            label="Confirm password"
            autoComplete="new-password"
            required
            minLength={passwordPolicy?.minimum_length}
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
