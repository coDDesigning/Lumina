import { useState } from 'react';
import type { FormEvent } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { authAPI } from '@/api/auth';
import { describeError } from '@/api/errors';
import { useAuth } from '@/context/AuthContext';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { IconButton } from '@/ui/IconButton';
import { Input } from '@/ui/Input';
import { AuthLayout } from './AuthLayout';
import { ResendVerification } from './ResendVerification';
import styles from './AuthLayout.module.css';

const MAX_PASSWORD_BYTES = 72;
// The server owns the policy and states it in full when it refuses; this is the
// floor the form can check before anybody waits for a round trip.
// See docs/authentication.md.
const MIN_PASSWORD_LENGTH = 8;
const SPECIAL_CHAR_REGEX = /[!@#$%^&*(),.?":{}|<>_\-+=[\]\\/;'`~]/;

function passwordByteLength(value: string): number {
  return new TextEncoder().encode(value).length;
}

export default function RegisterPage() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  // Set only where the deployment verifies addresses. The account already
  // exists and is signed in; what is missing is the credits, and the link is
  // what releases them.
  const [awaitingVerification, setAwaitingVerification] = useState<{
    email: string;
    message: string;
  } | null>(null);

  const { login } = useAuth();
  const navigate = useNavigate();

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (isSubmitting) {
      return;
    }
    setError(null);
    setPasswordError(null);

    if (passwordByteLength(password) > MAX_PASSWORD_BYTES) {
      setPasswordError('That password is too long. Keep it under 72 bytes.');
      return;
    }

    if (
      password.length < MIN_PASSWORD_LENGTH ||
      !/[A-Z]/.test(password) ||
      !SPECIAL_CHAR_REGEX.test(password)
    ) {
      setPasswordError(
        'Password must be at least 8 characters, with at least one uppercase letter and one special character.',
      );
      return;
    }

    if (password !== confirmPassword) {
      setPasswordError('Those two passwords do not match.');
      return;
    }

    setIsSubmitting(true);

    try {
      const registration = await authAPI.register(name.trim(), email.trim(), password);
      const session = await authAPI.login(email.trim(), password);
      await login(session.access_token);

      if (registration.email_verification_required && !registration.is_email_verified) {
        setAwaitingVerification({ email: registration.user_email, message: registration.message });
        return;
      }

      navigate('/dashboard', { replace: true });
    } catch (caught) {
      setError(
        describeError(caught, "We couldn't create that account. Try again in a moment.").message,
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  if (awaitingVerification) {
    return (
      <AuthLayout
        tone={2}
        documentTitle="Check your inbox"
        title="Check your inbox."
        subtitle={`We sent a confirmation link to ${awaitingVerification.email}.`}
        footer={<Link to="/dashboard">Skip for now and look around</Link>}
        note="Your account is ready to sign in to. The starting credits are added once you open the link, which is what keeps one person from opening fifty accounts."
      >
        <Alert tone="info" live="status">
          {awaitingVerification.message}
        </Alert>
        <ResendVerification
          knownEmail={awaitingVerification.email}
          label="Send the link again"
        />
      </AuthLayout>
    );
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
          type={showPassword ? 'text' : 'password'}
          autoComplete="new-password"
          required
          minLength={MIN_PASSWORD_LENGTH}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          disabled={isSubmitting}
          hint="At least 8 characters, with at least one uppercase letter and one special character."
          action={
            <IconButton
              label={showPassword ? 'Hide password' : 'Show password'}
              icon={showPassword ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
              size="sm"
              onClick={() => setShowPassword((prev) => !prev)}
              disabled={isSubmitting}
            />
          }
        />

        <Input
          label="Confirm password"
          type={showConfirmPassword ? 'text' : 'password'}
          autoComplete="new-password"
          required
          minLength={MIN_PASSWORD_LENGTH}
          value={confirmPassword}
          onChange={(event) => setConfirmPassword(event.target.value)}
          disabled={isSubmitting}
          error={passwordError ?? undefined}
          action={
            <IconButton
              label={showConfirmPassword ? 'Hide password' : 'Show password'}
              icon={showConfirmPassword ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
              size="sm"
              onClick={() => setShowConfirmPassword((prev) => !prev)}
              disabled={isSubmitting}
            />
          }
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
