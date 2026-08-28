import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { authAPI } from '@/api/auth';
import { describeError } from '@/api/errors';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { Input } from '@/ui/Input';
import { AuthLayout } from './AuthLayout';
import styles from './AuthLayout.module.css';

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [status, setStatus] = useState<'idle' | 'submitting' | 'success'>('idle');
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (status === 'submitting') {
      return;
    }
    setError(null);
    setStatus('submitting');

    try {
      const response = await authAPI.requestPasswordReset(email);
      setStatus('success');
      setMessage(response.message);
    } catch (caught) {
      setError(describeError(caught, 'Failed to request password reset.').message);
      setStatus('idle');
    }
  }

  return (
    <AuthLayout
      tone={0}
      documentTitle="Reset password"
      title="Reset your password."
      subtitle="We'll send you a link to choose a new one."
      footer={
        <>
          Remembered it? <Link to="/login">Sign in</Link>
        </>
      }
    >
      <form className={styles.form} onSubmit={handleSubmit}>
        {error ? (
          <Alert tone="destructive" live="alert">
            {error}
          </Alert>
        ) : null}

        {status === 'success' && message ? (
          <Alert tone="success" live="status">
            {message}
          </Alert>
        ) : (
          <>
            <Input
              label="Email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@university.edu"
              disabled={status === 'submitting'}
            />

            <Button
              type="submit"
              variant="primary"
              size="lg"
              fullWidth
              className={styles.submit}
              isLoading={status === 'submitting'}
              loadingLabel="Sending link"
            >
              Send link
            </Button>
          </>
        )}
      </form>
    </AuthLayout>
  );
}
