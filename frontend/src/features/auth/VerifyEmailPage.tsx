import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { authAPI } from '@/api/auth';
import { describeError } from '@/api/errors';
import { useAuth } from '@/context/AuthContext';
import { useCredits } from '@/context/CreditContext';
import { Alert } from '@/ui/Alert';
import { Spinner } from '@/ui/Spinner';
import { AuthLayout } from './AuthLayout';
import { ResendVerification } from './ResendVerification';

type Outcome =
  | { kind: 'no-token' }
  | { kind: 'redeeming' }
  | { kind: 'verified'; message: string; creditsGranted: number | null }
  | { kind: 'failed'; message: string };

/**
 * Where an emailed verification link lands.
 *
 * Unauthenticated on purpose: the link is opened from a mail client that may be
 * signed in to nothing, and the token is the proof. When there is a session it
 * refreshes the user and the balance, because verifying is the moment the
 * introductory credits arrive and a stale zero would be a lie.
 */
export default function VerifyEmailPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token') ?? '';
  const { isAuthenticated, refreshUser } = useAuth();
  const { refresh: refreshCredits } = useCredits();
  const [outcome, setOutcome] = useState<Outcome>(
    token ? { kind: 'redeeming' } : { kind: 'no-token' },
  );
  // A link is single use, so a double mount in development must not spend the
  // token once and then report the second attempt as an invalid link.
  const redeemed = useRef<string | null>(null);

  useEffect(() => {
    if (!token || redeemed.current === token) {
      return;
    }
    redeemed.current = token;

    void (async () => {
      try {
        const result = await authAPI.verifyEmail(token);
        setOutcome({
          kind: 'verified',
          message: result.message,
          creditsGranted: result.credits_granted,
        });
        void refreshUser();
        void refreshCredits();
      } catch (caught) {
        setOutcome({
          kind: 'failed',
          message: describeError(
            caught,
            'This verification link is invalid or has expired. Request a new one.',
          ).message,
        });
      }
    })();
  }, [token, refreshCredits, refreshUser]);

  return (
    <AuthLayout
      tone={3}
      documentTitle="Verify your email"
      title="Confirm your address."
      subtitle="One click finishes setting up your account."
      footer={
        outcome.kind === 'verified' ? (
          <Link to={isAuthenticated ? '/dashboard' : '/login'}>
            {isAuthenticated ? 'Go to your courses' : 'Sign in'}
          </Link>
        ) : (
          <>
            Already confirmed? <Link to="/login">Sign in</Link>
          </>
        )
      }
    >
      {outcome.kind === 'redeeming' ? <Spinner label="Confirming your address" /> : null}

      {outcome.kind === 'verified' ? (
        <Alert tone="success" title="Address confirmed" live="status">
          {outcome.message}
          {outcome.creditsGranted !== null
            ? ` Your ${outcome.creditsGranted} starting credits are in your account.`
            : ''}
        </Alert>
      ) : null}

      {outcome.kind === 'failed' ? (
        <>
          <Alert tone="destructive" live="alert">
            {outcome.message}
          </Alert>
          <ResendVerification />
        </>
      ) : null}

      {outcome.kind === 'no-token' ? (
        <>
          <Alert tone="warning" live="status">
            This link is missing its token. Mail clients sometimes cut a long link in two — ask
            for a new one and open it in a single piece.
          </Alert>
          <ResendVerification />
        </>
      ) : null}
    </AuthLayout>
  );
}
