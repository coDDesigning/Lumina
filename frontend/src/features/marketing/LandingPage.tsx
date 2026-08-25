import { Check, FileText } from 'lucide-react';
import { Link, Navigate } from 'react-router-dom';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useAuth } from '@/context/AuthContext';
import { Badge } from '@/ui/Badge';
import { BrandLockup } from '@/ui/Brandmark';
import { Breath } from '@/ui/Breath';
import { CourseChip, CourseLight } from '@/ui/CourseLight';
import { ExternalLinkButton, LinkButton } from '@/ui/LinkButton';
import styles from './LandingPage.module.css';

const PIPELINE = [
  {
    label: 'First',
    lead: 'You upload.',
    body: 'PDF, text or Markdown. Drop in ten files at once if you like.',
  },
  {
    label: 'Then',
    lead: 'Lumina reads.',
    body: 'Including scans and photos of handwriting, using OCR when the text is not selectable.',
  },
  {
    label: 'Then',
    lead: 'You ask for something.',
    body: 'A guide, a quiz, flashcards, or just a question about the material.',
  },
  {
    label: 'And',
    lead: 'It shows its sources.',
    body: 'Every answer names the passages it used and how well they matched.',
  },
];

const CAPABILITIES = [
  {
    title: 'Reading your files',
    body: 'PDF, TXT and Markdown. Scanned pages go through OCR automatically. Duplicates are caught by content, not filename.',
  },
  {
    title: 'Study guides',
    body: 'Key points, terms, common mistakes, exam tips, prerequisites and objectives — plus how much of your material was actually covered.',
  },
  {
    title: 'Quizzes that grade',
    body: 'Multiple choice, true/false, short answer and written. Written answers get graded and get feedback — and when a mark cannot be given, it says so rather than marking you wrong.',
  },
  {
    title: 'Asking and tutoring',
    body: 'Two separate threads per course: direct answers, or a tutor that walks you there. Both search only that one course.',
  },
  {
    title: 'Progress you can act on',
    body: 'Per-topic mastery built from real attempts, and the topics you keep missing — so the next session has an obvious starting point.',
  },
];

export default function LandingPage() {
  const { isAuthenticated, isLoading } = useAuth();
  useDocumentTitle(undefined);

  if (isLoading) {
    return <div className={styles.page} />;
  }

  if (isAuthenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  return (
    <div className={styles.page}>
      <header className={styles.nav}>
        <Link to="/" aria-label="Lumina home">
          <BrandLockup />
        </Link>
        <nav className={styles.navLinks} aria-label="About Lumina">
          <a href="#how-it-works">How it works</a>
          <a href="#capabilities">What it does</a>
          <a href="#privacy">Privacy</a>
        </nav>
        <div className={styles.navActions}>
          <LinkButton to="/login" variant="ghost" size="sm">
            Sign in
          </LinkButton>
          <LinkButton to="/register" variant="primary" size="sm">
            Create account
          </LinkButton>
        </div>
      </header>

      <CourseLight courseId={0} as="section" className={styles.hero}>
        <div className={styles.heroInner}>
          <Badge tone="accent">Open source · Self-hostable · Free to run yourself</Badge>
          <h1 className={styles.heroTitle}>
            Turn the PDFs you already have into the studying you actually need.
          </h1>
          <p className={styles.heroLede}>
            Upload your lectures, notes and past papers. Lumina reads them — scanned pages included
            — and builds study guides, quizzes and answers grounded in your own material, never in
            something it made up.
          </p>
          <div className={styles.heroActions}>
            <LinkButton to="/register" variant="primary" size="lg">
              Start with one course
            </LinkButton>
            <ExternalLinkButton href="#privacy" variant="secondary" size="lg">
              Run it on your own machine
            </ExternalLinkButton>
          </div>
          <p className={styles.heroFootnote}>
            No card needed. 20 credits a month on the hosted version — unmetered when you self-host.
          </p>
        </div>
      </CourseLight>

      <section className={styles.section} id="how-it-works">
        <p className={styles.eyebrow}>How it works</p>
        <h2 className={styles.sectionTitle}>Four steps, in this order, every time.</h2>
        <ol className={styles.flow}>
          {PIPELINE.map((step) => (
            <li key={step.lead} className={styles.flowStep}>
              <span className={styles.flowLabel}>{step.label}</span>
              <p className={styles.flowBody}>
                <b>{step.lead}</b> {step.body}
              </p>
            </li>
          ))}
        </ol>
      </section>

      <section className={styles.section}>
        <p className={styles.eyebrow}>The workspace</p>
        <h2 className={styles.sectionTitle}>One course, one place, one thread.</h2>
        <p className={styles.sectionLede}>
          Your sources on the left, the conversation in the middle, everything Lumina has made for
          you on the right.
        </p>

        <div className={styles.preview} aria-hidden="true">
          <CourseLight courseId={0} className={styles.previewHeader}>
            <CourseChip courseId={0} />
            <span className={styles.previewTitle}>CS 3410 · Computer Architecture</span>
          </CourseLight>
          <div className={styles.previewBody}>
            <div className={styles.previewSide}>
              <span className={styles.eyebrow}>Sources</span>
              <span className={styles.previewSourceRow}>
                <Badge tone="success" icon={<Check aria-hidden="true" />}>
                  Ready
                </Badge>
                <span className={styles.previewSourceName}>lecture-07-pipelining.pdf</span>
              </span>
              <span className={styles.previewSourceRow}>
                <Badge tone="success" icon={<Check aria-hidden="true" />}>
                  Ready
                </Badge>
                <span className={styles.previewSourceName}>cs3410-syllabus.pdf</span>
              </span>
              <span className={styles.previewSourceRow}>
                <Breath />
                <span className={styles.previewSourceName}>midterm-2023.pdf</span>
              </span>
            </div>

            <div className={styles.previewThread}>
              <p className={styles.previewQuestion}>
                Why does a 5-stage pipeline still stall after forwarding?
              </p>
              <p className={styles.previewAnswer}>
                Because a load&apos;s value only exists at the end of MEM. Forwarding covers
                ALU-to-ALU dependencies, but a load-use pair still costs one bubble…
              </p>
              <span className={styles.previewProvenance}>
                <FileText aria-hidden="true" style={{ width: '0.75rem', height: '0.75rem' }} />
                Read <b>5 of 63 passages</b> · lecture-07 pp. 12–18
              </span>
            </div>

            <div className={styles.previewSide}>
              <span className={styles.eyebrow}>Made for you</span>
              <span className={styles.previewSourceRow}>Study guide · Pipelining</span>
              <span className={styles.previewSourceRow}>Quiz · 10 questions</span>
              <span className={styles.previewSourceRow}>28 flashcards</span>
            </div>
          </div>
        </div>
      </section>

      <section className={styles.section} id="capabilities">
        <p className={styles.eyebrow}>What works today</p>
        <h2 className={styles.sectionTitle}>
          Everything on this list is built. Nothing here is a preview.
        </h2>
        <div className={styles.capabilities}>
          {CAPABILITIES.map((capability) => (
            <article key={capability.title} className={styles.capability}>
              <h3 className={styles.capabilityTitle}>{capability.title}</h3>
              <p className={styles.capabilityBody}>{capability.body}</p>
            </article>
          ))}
          <article className={`${styles.capability} ${styles.capabilityMuted}`}>
            <h3 className={styles.capabilityTitle}>Not built yet</h3>
            <p className={styles.capabilityBody}>
              Audio and video, spaced repetition, editing a generated guide, and Exam Mode. They are
              on the roadmap and they are not in the product.
            </p>
          </article>
        </div>
      </section>

      <footer className={styles.footer} id="privacy">
        <div className={styles.footerInner}>
          <span>
            Lumina is open source. Self-host it with Docker, SQLite and a local Ollama model —
            nothing you upload leaves your machine.
          </span>
          <span className={styles.footerLinks}>
            <a href="https://github.com/coDDesigning/Lumina">GitHub</a>
            <Link to="/login">Sign in</Link>
          </span>
        </div>
      </footer>
    </div>
  );
}
