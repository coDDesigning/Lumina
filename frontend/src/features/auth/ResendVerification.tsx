import { useState } from 'react';
import type { FormEvent } from 'react';
import { authAPI } from '@/api/auth';
import { describeError } from '@/api/errors';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { Input } from '@/ui/Input';
import styles from './AuthLayout.module.css';

export interface ResendVerificationProps {
  /**
   * The address to send to, when we already know it. Given one, the control is
   * a single button: asking somebody to retype what they typed a moment ago
   * only invites a typo into the one field that has to be right.
   */
  knownEmail?: string;
  label?: string;
}

export function ResendVerification({
  knownEmail,
  label = 'Send a new link',
}: ResendVerificationProps) {
  const [email, setEmail] = useState(knownEmail ?? '');
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSending, setIsSending] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (isSending) {
      return;
    }
    setNotice(null);
    setError(null);
    setIsSending(true);

    try {
      const result = await authAPI.resendVerification(email.trim());
      setNotice(result.message);
    } catch (caught) {
      setError(
        describeError(caught, "We couldn't send a new link. Try again in a moment.").message,
      );
    } finally {
      setIsSending(false);
    }
  }

  return (
    <form className={styles.form} onSubmit={handleSubmit}>
      {error ? (
        <Alert tone="destructive" live="alert">
          {error}
        </Alert>
      ) : null}

      {notice ? (
        <Alert tone="success" live="status">
          {notice}
        </Alert>
      ) : null}

      {knownEmail ? null : (
        <Input
          label="Email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@university.edu"
          disabled={isSending}
        />
      )}

      <Button
        type="submit"
        variant="secondary"
        size="lg"
        fullWidth
        className={styles.submit}
        isLoading={isSending}
        loadingLabel="Sending a new link"
      >
        {label}
      </Button>
    </form>
  );
}
