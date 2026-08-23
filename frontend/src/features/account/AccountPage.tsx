import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { LogOut } from 'lucide-react';
import { describeError } from '@/api/errors';
import { EDUCATION_LEVEL_LABELS } from '@/api/types';
import type { EducationLevel } from '@/api/types';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useTheme } from '@/app/themeContext';
import type { ThemePreference } from '@/app/themeContext';
import { userAPI } from '@/api/user';
import { useAuth } from '@/context/AuthContext';
import { Alert } from '@/ui/Alert';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { Card } from '@/ui/Card';
import { Select } from '@/ui/Input';
import { PageHeader } from '@/ui/PageHeader';
import { AiPreferencesSection } from './AiPreferencesSection';
import { ProfileKnowledgeSection } from './ProfileKnowledgeSection';
import styles from './AccountPage.module.css';

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) {
    return '?';
  }
  return parts
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('');
}

export default function AccountPage() {
  const { user, logout, refreshUser } = useAuth();
  const { preference, setPreference } = useTheme();
  const navigate = useNavigate();
  useDocumentTitle('Account');

  const [levelError, setLevelError] = useState<string | null>(null);
  const [levelNotice, setLevelNotice] = useState<string | null>(null);

  async function handleLevelChange(level: EducationLevel) {
    setLevelError(null);
    setLevelNotice(null);
    try {
      await userAPI.updateEducationLevel(level);
      await refreshUser();
      setLevelNotice('Education level updated.');
    } catch (caught) {
      setLevelError(describeError(caught, "That couldn't be saved.").message);
    }
  }

  function handleSignOut() {
    logout();
    navigate('/login');
  }

  return (
    <div className={styles.page}>
      <PageHeader crumbs={[{ label: 'Account' }]} />

      <div className={styles.body}>
        <h1 className={styles.title}>Account</h1>
        <p className={styles.subtitle}>Who you are, what Lumina knows about you, and how it generates.</p>

        <Card className={styles.identity}>
          <span className={styles.avatar} aria-hidden="true">
            {initials(user?.name ?? '')}
          </span>
          <div className={styles.identityText}>
            <p className={styles.name}>{user?.name}</p>
            <p className={styles.email}>{user?.email}</p>
            <div className={styles.identityMeta}>
              {user?.role ? <Badge tone="accent">{user.role}</Badge> : null}
            </div>
          </div>
          <Button variant="ghost" icon={<LogOut aria-hidden="true" />} onClick={handleSignOut}>
            Sign out
          </Button>
        </Card>

        <section className={styles.section}>
          <h2 className={styles.sectionHeading}>Your level</h2>
          <p className={styles.sectionLede}>
            Sets how deeply explanations are pitched by default. A course can override it.
          </p>
          {levelError ? (
            <Alert tone="destructive" live="alert">
              {levelError}
            </Alert>
          ) : null}
          {levelNotice ? (
            <Alert tone="success" live="status">
              {levelNotice}
            </Alert>
          ) : null}
          <Select
            label="What are you studying at?"
            value={user?.education_level ?? 'unspecified'}
            onChange={(event) => void handleLevelChange(event.target.value as EducationLevel)}
          >
            {(Object.keys(EDUCATION_LEVEL_LABELS) as EducationLevel[]).map((level) => (
              <option key={level} value={level}>
                {EDUCATION_LEVEL_LABELS[level]}
              </option>
            ))}
          </Select>
        </section>

        <ProfileKnowledgeSection />

        <AiPreferencesSection />

        <section className={styles.section}>
          <h2 className={styles.sectionHeading}>Appearance</h2>
          <p className={styles.sectionLede}>
            System follows whatever your device is set to.
          </p>
          <Select
            label="Theme"
            value={preference}
            onChange={(event) => setPreference(event.target.value as ThemePreference)}
          >
            <option value="system">Match my system</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </Select>
        </section>
      </div>
    </div>
  );
}
