import { useState } from 'react';
import type { FormEvent } from 'react';
import { Navigate } from 'react-router-dom';
import { Eye, EyeOff, KeyRound, Sparkles, Trash2 } from 'lucide-react';
import { describeError } from '@/api/errors';
import { queryKeys } from '@/api/queryKeys';
import { userAPI } from '@/api/user';
import type { UserApiKeys } from '@/api/types';
import { useAuth } from '@/context/AuthContext';
import { queryCache } from '@/lib/query/cache';
import { useQuery } from '@/lib/query/useQuery';
import { Alert } from '@/ui/Alert';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { Card } from '@/ui/Card';
import { IconButton } from '@/ui/IconButton';
import { Input } from '@/ui/Input';
import { Skeleton } from '@/ui/Skeleton';
import { useToast } from '@/ui/toastContext';
import styles from './AccountPage.module.css';

export default function AccountApiKeysPage() {
  const { user } = useAuth();
  const { showToast } = useToast();

  const [openaiKey, setOpenaiKey] = useState('');
  const [geminiKey, setGeminiKey] = useState('');
  const [anthropicKey, setAnthropicKey] = useState('');

  const [showOpenaiKey, setShowOpenaiKey] = useState(false);
  const [showGeminiKey, setShowGeminiKey] = useState(false);
  const [showAnthropicKey, setShowAnthropicKey] = useState(false);

  const [status, setStatus] = useState<'idle' | 'submitting' | 'clearing'>('idle');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const apiKeysQuery = useQuery<UserApiKeys>({
    key: queryKeys.userApiKeys(),
    fetcher: ({ signal }) => userAPI.getApiKeys({ signal }),
    fallbackMessage: "We couldn't load your API key configuration.",
  });

  const data = apiKeysQuery.data;
  const isLoading = apiKeysQuery.status === 'pending' || apiKeysQuery.status === 'idle';
  const queryError = apiKeysQuery.error?.message ?? null;

  if (user && user.role?.toLowerCase() !== 'admin') {
    return <Navigate to="/account" replace />;
  }

  const hasAnyConfiguredKey = Boolean(
    data?.has_openai_key || data?.has_gemini_key || data?.has_anthropic_key,
  );

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (status === 'submitting') {
      return;
    }

    setError(null);
    setNotice(null);

    const trimmedOpenai = openaiKey.trim();
    const trimmedGemini = geminiKey.trim();
    const trimmedAnthropic = anthropicKey.trim();

    if (trimmedOpenai !== '') {
      if (!trimmedOpenai.startsWith('sk-') || !/^sk-[A-Za-z0-9_-]+$/.test(trimmedOpenai)) {
        setError("OpenAI API key must start with 'sk-'.");
        return;
      }
    }

    if (trimmedAnthropic !== '') {
      if (!trimmedAnthropic.startsWith('sk-ant-') || !/^sk-ant-[A-Za-z0-9_-]+$/.test(trimmedAnthropic)) {
        setError("Anthropic API key must start with 'sk-ant-'.");
        return;
      }
    }

    if (trimmedGemini !== '') {
      if (!/^[A-Za-z0-9_-]+$/.test(trimmedGemini)) {
        setError('Gemini API key contains invalid characters.');
        return;
      }
    }

    setStatus('submitting');

    try {
      const payload: {
        openai_api_key?: string;
        gemini_api_key?: string;
        anthropic_api_key?: string;
      } = {};

      if (trimmedOpenai !== '') {
        payload.openai_api_key = trimmedOpenai;
      }
      if (trimmedGemini !== '') {
        payload.gemini_api_key = trimmedGemini;
      }
      if (trimmedAnthropic !== '') {
        payload.anthropic_api_key = trimmedAnthropic;
      }

      await userAPI.updateApiKeys(payload);
      void queryCache.invalidate(queryKeys.userApiKeys());
      void queryCache.invalidate(queryKeys.models());

      setOpenaiKey('');
      setGeminiKey('');
      setAnthropicKey('');
      setStatus('idle');
      setNotice('Your API keys have been saved securely.');
      showToast({
        tone: 'success',
        title: 'API keys updated',
        message: 'Your personal API keys have been saved securely.',
      });
    } catch (caught) {
      setError(describeError(caught, "Couldn't save API keys.").message);
      setStatus('idle');
    }
  }

  async function handleClearSingleKey(provider: 'openai' | 'gemini' | 'anthropic') {
    setError(null);
    setNotice(null);
    setStatus('clearing');

    try {
      const payload = {
        [`${provider}_api_key`]: '',
      };
      await userAPI.updateApiKeys(payload);
      void queryCache.invalidate(queryKeys.userApiKeys());
      void queryCache.invalidate(queryKeys.models());

      if (provider === 'openai') setOpenaiKey('');
      if (provider === 'gemini') setGeminiKey('');
      if (provider === 'anthropic') setAnthropicKey('');

      setStatus('idle');
      const providerLabel =
        provider === 'openai' ? 'OpenAI' : provider === 'gemini' ? 'Gemini' : 'Anthropic';
      setNotice(`${providerLabel} API key removed.`);
      showToast({
        tone: 'success',
        title: 'Key removed',
        message: `${providerLabel} API key has been cleared.`,
      });
    } catch (caught) {
      setError(describeError(caught, "Couldn't clear API key.").message);
      setStatus('idle');
    }
  }

  async function handleClearAll() {
    setError(null);
    setNotice(null);
    setStatus('clearing');

    try {
      await userAPI.updateApiKeys({
        openai_api_key: '',
        gemini_api_key: '',
        anthropic_api_key: '',
      });
      void queryCache.invalidate(queryKeys.userApiKeys());
      void queryCache.invalidate(queryKeys.models());

      setOpenaiKey('');
      setGeminiKey('');
      setAnthropicKey('');
      setStatus('idle');
      setNotice('All custom API keys have been cleared.');
      showToast({
        tone: 'success',
        title: 'API keys cleared',
        message: 'All personal API keys have been removed.',
      });
    } catch (caught) {
      setError(describeError(caught, "Couldn't clear API keys.").message);
      setStatus('idle');
    }
  }

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionHeading}>API keys</h2>
      <p className={styles.sectionLede}>
        Bring your own API keys for OpenAI, Google Gemini, and Anthropic Claude. When configured,
        Lumina routes AI generations through your personal keys. Leave blank to use system defaults.
      </p>

      {queryError ? (
        <Alert tone="destructive" live="alert">
          {queryError}
        </Alert>
      ) : null}

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

      {isLoading ? (
        <Skeleton variant="block" />
      ) : (
        <form className={styles.formGrid} onSubmit={handleSubmit}>
          {/* OpenAI */}
          <Card padding="md" className={styles.stack}>
            <div className={styles.sectionHead}>
              <div>
                <p className={styles.rowTitle}>OpenAI</p>
                <p className={styles.rowBody}>Used for GPT-4o, GPT-5, and OpenAI text models.</p>
              </div>
              <div className={styles.rowActions}>
                {data?.has_openai_key ? (
                  <>
                    <Badge tone="success">Configured</Badge>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => handleClearSingleKey('openai')}
                      disabled={status !== 'idle'}
                      icon={<Trash2 aria-hidden="true" />}
                    >
                      Clear key
                    </Button>
                  </>
                ) : (
                  <Badge tone="neutral">System default</Badge>
                )}
              </div>
            </div>

            <Input
              label="OpenAI API Key"
              type={showOpenaiKey ? 'text' : 'password'}
              autoComplete="off"
              value={openaiKey}
              onChange={(event) => {
                setOpenaiKey(event.target.value);
                setError(null);
                setNotice(null);
              }}
              placeholder={data?.has_openai_key ? (data.openai_api_key ?? 'sk-...') : 'sk-...'}
              hint="Starts with sk-"
              disabled={status !== 'idle'}
              action={
                <IconButton
                  label={showOpenaiKey ? 'Hide OpenAI API key' : 'Show OpenAI API key'}
                  icon={showOpenaiKey ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
                  onClick={() => setShowOpenaiKey((v) => !v)}
                  size="sm"
                />
              }
            />
          </Card>

          {/* Gemini */}
          <Card padding="md" className={styles.stack}>
            <div className={styles.sectionHead}>
              <div>
                <p className={styles.rowTitle}>Google Gemini</p>
                <p className={styles.rowBody}>Used for Gemini 1.5 Flash, Gemini 2.0, and Google models.</p>
              </div>
              <div className={styles.rowActions}>
                {data?.has_gemini_key ? (
                  <>
                    <Badge tone="success">Configured</Badge>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => handleClearSingleKey('gemini')}
                      disabled={status !== 'idle'}
                      icon={<Trash2 aria-hidden="true" />}
                    >
                      Clear key
                    </Button>
                  </>
                ) : (
                  <Badge tone="neutral">System default</Badge>
                )}
              </div>
            </div>

            <Input
              label="Gemini API Key"
              type={showGeminiKey ? 'text' : 'password'}
              autoComplete="off"
              value={geminiKey}
              onChange={(event) => {
                setGeminiKey(event.target.value);
                setError(null);
                setNotice(null);
              }}
              placeholder={data?.has_gemini_key ? (data.gemini_api_key ?? 'AIzaSy...') : 'AIzaSy...'}
              hint="Starts with AIzaSy"
              disabled={status !== 'idle'}
              action={
                <IconButton
                  label={showGeminiKey ? 'Hide Gemini API key' : 'Show Gemini API key'}
                  icon={showGeminiKey ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
                  onClick={() => setShowGeminiKey((v) => !v)}
                  size="sm"
                />
              }
            />
          </Card>

          {/* Anthropic Claude */}
          <Card padding="md" className={styles.stack}>
            <div className={styles.sectionHead}>
              <div>
                <p className={styles.rowTitle}>Anthropic Claude</p>
                <p className={styles.rowBody}>Used for Claude 3.5 Sonnet, Claude 3.7, and Anthropic models.</p>
              </div>
              <div className={styles.rowActions}>
                {data?.has_anthropic_key ? (
                  <>
                    <Badge tone="success">Configured</Badge>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => handleClearSingleKey('anthropic')}
                      disabled={status !== 'idle'}
                      icon={<Trash2 aria-hidden="true" />}
                    >
                      Clear key
                    </Button>
                  </>
                ) : (
                  <Badge tone="neutral">System default</Badge>
                )}
              </div>
            </div>

            <Input
              label="Anthropic API Key"
              type={showAnthropicKey ? 'text' : 'password'}
              autoComplete="off"
              value={anthropicKey}
              onChange={(event) => {
                setAnthropicKey(event.target.value);
                setError(null);
                setNotice(null);
              }}
              placeholder={data?.has_anthropic_key ? (data.anthropic_api_key ?? 'sk-ant-...') : 'sk-ant-...'}
              hint="Starts with sk-ant-"
              disabled={status !== 'idle'}
              action={
                <IconButton
                  label={showAnthropicKey ? 'Hide Anthropic API key' : 'Show Anthropic API key'}
                  icon={showAnthropicKey ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
                  onClick={() => setShowAnthropicKey((v) => !v)}
                  size="sm"
                />
              }
            />
          </Card>

          <div className={styles.actions}>
            <Button
              type="submit"
              variant="primary"
              isLoading={status === 'submitting'}
              loadingLabel="Saving"
              disabled={
                status !== 'idle' ||
                (openaiKey.trim() === '' && geminiKey.trim() === '' && anthropicKey.trim() === '')
              }
              icon={<KeyRound aria-hidden="true" />}
            >
              Save API keys
            </Button>

            {hasAnyConfiguredKey ? (
              <Button
                type="button"
                variant="ghost"
                onClick={handleClearAll}
                isLoading={status === 'clearing'}
                loadingLabel="Clearing"
                disabled={status !== 'idle'}
                icon={<Trash2 aria-hidden="true" />}
              >
                Clear all keys
              </Button>
            ) : null}
          </div>

          <p className={styles.footnote}>
            <Sparkles aria-hidden="true" style={{ verticalAlign: 'middle', marginRight: 'var(--space-1)' }} />
            Keys are encrypted using symmetric application secrets before being saved and are never exposed in plaintext.
          </p>
        </form>
      )}
    </section>
  );
}
