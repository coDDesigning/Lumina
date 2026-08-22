import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { authAPI } from '@/api/auth';
import { describeError } from '@/api/errors';
import { useAuth } from '@/context/AuthContext';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { Input } from '@/ui/Input';
import { AuthLayout } from './AuthLayout';
import styles from './AuthLayout.module.css';

interface LocationState {
  from?: { pathname?: string };
}

export default function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const from = (location.state as LocationState | null)?.from?.pathname ?? '/dashboard';

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);

    try {
      const response = await authAPI.login(email, password);
      await login(response.access_token);
      navigate(from, { replace: true });
    } catch (caught) {
      setError(
        describeError(caught, "That email and password don't match. Check them and try again.")
          .message,
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AuthLayout
      tone={0}
      documentTitle="Sign in"
      title="Welcome back."
      subtitle="Pick up where you left off."
      footer={
        <>
          New here? <Link to="/register">Create an account</Link>
        </>
      }
    >
      <form className={styles.form} onSubmit={handleSubmit}>
        {error ? (
          <Alert tone="destructive" live="alert">
            {error}
          </Alert>
        ) : null}

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
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          disabled={isSubmitting}
        />

        <Button
          type="submit"
          variant="primary"
          size="lg"
          fullWidth
          className={styles.submit}
          isLoading={isSubmitting}
          loadingLabel="Signing in"
        >
          Sign in
        </Button>
      </form>
    </AuthLayout>
  );
}
