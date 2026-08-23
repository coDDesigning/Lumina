import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { authAPI } from '@/api/auth';
import { describeError } from '@/api/errors';
import { useAuth } from '@/context/AuthContext';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { Input } from '@/ui/Input';
import { AuthLayout } from './AuthLayout';
import styles from './AuthLayout.module.css';

const MAX_PASSWORD_BYTES = 72;

function passwordByteLength(value: string): number {
  return new TextEncoder().encode(value).length;
}

export default function RegisterPage() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const { login } = useAuth();
  const navigate = useNavigate();

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setPasswordError(null);

    if (passwordByteLength(password) > MAX_PASSWORD_BYTES) {
      setPasswordError('That password is too long. Keep it under 72 bytes.');
      return;
    }

    if (password !== confirmPassword) {
      setPasswordError('Those two passwords do not match.');
      return;
    }

    setIsSubmitting(true);

    try {
      await authAPI.register(name.trim(), email.trim(), password);
      const session = await authAPI.login(email.trim(), password);
      await login(session.access_token);
      navigate('/dashboard', { replace: true });
    } catch (caught) {
      setError(
        describeError(caught, "We couldn't create that account. Try again in a moment.").message,
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AuthLayout
      tone={2}
      documentTitle="Create an account"
      title="Make an account."
      subtitle="One course is enough to see whether this helps."
      footer={
        <>
          Already have an account? <Link to="/login">Sign in</Link>
        </>
      }
      note="Your uploads are never used to train anything. You can delete a course, and everything in it, permanently at any time."
    >
      <form className={styles.form} onSubmit={handleSubmit}>
        {error ? (
          <Alert tone="destructive" live="alert">
            {error}
          </Alert>
        ) : null}

        <Input
          label="Name"
          autoComplete="name"
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
          disabled={isSubmitting}
        />

        <Input
          label="Email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@university.edu"
          disabled={isSubmitting}
        />

        <Input
          label="Password"
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          disabled={isSubmitting}
          hint="At least 8 characters, up to 72 bytes."
        />

        <Input
          label="Confirm password"
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          value={confirmPassword}
          onChange={(event) => setConfirmPassword(event.target.value)}
          disabled={isSubmitting}
          error={passwordError ?? undefined}
        />

        <Button
          type="submit"
          variant="primary"
          size="lg"
          fullWidth
          className={styles.submit}
          isLoading={isSubmitting}
          loadingLabel="Creating your account"
        >
          Create account
        </Button>
      </form>
    </AuthLayout>
  );
}
