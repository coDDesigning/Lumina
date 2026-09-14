import { Link } from 'react-router-dom';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { BrandLockup } from '@/ui/Brandmark';
import { LegalFooter } from './LegalFooter';
import { POLICY_EFFECTIVE_DATE, POLICY_VERSION } from './legalDocuments';
import type { LegalDocument } from './legalDocuments';
import styles from './LegalPage.module.css';

type LegalPageProps = {
  document: LegalDocument;
};

export function LegalPage({ document }: LegalPageProps) {
  useDocumentTitle(document.title);

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <Link to="/" aria-label="Lumina home">
          <BrandLockup />
        </Link>
      </header>
      <main id="main" className={styles.main}>
        <Link className={styles.back} to="/">
          &larr; Back to Lumina
        </Link>
        <h1>{document.title}</h1>
        <p className={styles.summary}>{document.summary}</p>
        <p className={styles.metadata}>
          Effective {POLICY_EFFECTIVE_DATE} · Version {POLICY_VERSION}
        </p>
        <div className={styles.sections}>
          {document.sections.map((section) => (
            <section key={section.heading}>
              <h2>{section.heading}</h2>
              {section.content}
            </section>
          ))}
        </div>
      </main>
      <LegalFooter />
    </div>
  );
}
