import { useState } from 'react';
import { describeError } from '@/api/errors';
import { EDUCATION_LEVEL_LABELS } from '@/api/types';
import type { EducationLevel } from '@/api/types';
import { userAPI } from '@/api/user';
import { useAuth } from '@/context/AuthContext';
import { Alert } from '@/ui/Alert';
import { Select } from '@/ui/Input';
import styles from './AccountPage.module.css';

export default function AccountYouPage() {
  const { user, refreshUser } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function handleLevelChange(level: EducationLevel) {
    setError(null);
    setNotice(null);
    try {
      await userAPI.updateEducationLevel(level);
      await refreshUser();
      setNotice('Education level updated.');
    } catch (caught) {
      setError(describeError(caught, "That couldn't be saved.").message);
    }
  }

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionHeading}>Your level</h2>
      <p className={styles.sectionLede}>
        Sets how deeply explanations are pitched by default. A course can override it.
      </p>
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
  );
}
