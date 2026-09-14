import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

export const POLICY_EFFECTIVE_DATE = '12 September 2026';
export const POLICY_VERSION = '1.0';

export type LegalSection = {
  heading: string;
  content: ReactNode;
};

export type LegalDocument = {
  title: string;
  summary: string;
  sections: LegalSection[];
};

const contact = <a href="mailto:info@lumina-study.com">info@lumina-study.com</a>;

export const privacyPolicy: LegalDocument = {
  title: 'Privacy Notice',
  summary:
    'This notice explains what Lumina processes, why it is needed, where it goes, and the choices available in hosted and self-hosted deployments.',
  sections: [
    {
      heading: 'Who this notice covers',
      content: (
        <>
          <p>
            The hosted Lumina service is operated by the coDDesigning project group. Contact us at
            {' '}{contact}. In a self-hosted installation, the person or organisation operating that
            installation controls the data and should provide its own notice. coDDesigning does not
            receive self-hosted data merely because the software is installed.
          </p>
          <p>
            The legal bases described below are the intended bases and remain subject to final legal
            review for each country in which hosted Lumina is offered.
          </p>
        </>
      ),
    },
    {
      heading: 'Data we process and why',
      content: (
        <ul>
          <li><strong>Account and security data:</strong> name, email, password hash, account and role identifiers, verification state, policy acknowledgements, encrypted bring-your-own-provider keys, and token records. We use these to create, secure, recover, and administer accounts.</li>
          <li><strong>Course data:</strong> course names, subject and education level, semester, exam dates, syllabus text, topics, settings, and ownership metadata. We use these to organise and tailor the workspace.</li>
          <li><strong>Uploaded material:</strong> PDFs, text and Markdown files, images, notes, past papers, and other study material. We store and process it only to provide requested product functions.</li>
          <li><strong>Derived document data:</strong> extracted text, page records, OCR and visual descriptions, chunks, local embeddings, vector representations, citations, content hashes, and processing metadata. We use it to search the correct course and ground generated material.</li>
          <li><strong>Generated learning data:</strong> guides, flashcards, quizzes and questions, answers and grading feedback, explanations, tutor and course Q&amp;A conversations, Exam Mode analyses and plans, and generation-job records.</li>
          <li><strong>Learning and account-operation data:</strong> quiz sessions and attempts, answers, scores, timing where supplied, progress and mastery inferences, activity history, credit balance and transaction history.</li>
          <li><strong>Technical and safety data:</strong> request and correlation identifiers; rate-limit keys that are unsalted hashes of IP addresses or sign-in email addresses, or internal account IDs; operational and security events that can include your account ID and, when an AI response fails validation, a short excerpt of that AI output; client error reports limited to the route, app version, error class, request ID, and a fingerprint; and AI usage telemetry that records provider, model, token counts, timing, and outcome but no prompt or response text. Lumina does not store raw IP addresses or browser user-agent strings in its database or application logs, although network and browser information necessarily reaches hosting infrastructure and, after ad consent, the advertising provider.</li>
        </ul>
      ),
    },
    {
      heading: 'Purposes and legal bases',
      content: (
        <p>
          We process account, course, content, generation, and learning data to perform the service
          requested by the user; security and operational data to protect, troubleshoot, and maintain
          Lumina; and limited records to meet legal obligations and establish or defend legal claims.
          Depending on the jurisdiction, these activities are expected to rely on performance of a
          contract, legitimate interests in operating a safe service, legal obligations, or consent
          where the law requires it. Optional advertising identifiers are processed only after the
          applicable user choice. Withdrawing optional consent does not affect strictly necessary
          account and security processing.
        </p>
      ),
    },
    {
      heading: 'Where data is stored and who receives it',
      content: (
        <>
          <p>
            Hosted Lumina currently uses Amazon Web Services in the eu-central-1 region for its core
            application, PostgreSQL database, object storage, and CloudWatch logs. CloudFront may process
            requests at global edge locations. Google's SMTP service delivers hosted mail and receives the
            email addresses and message content needed to send verification, recovery, and service
            mail.
          </p>
          <p>
            When configured for a request, Google Gemini, OpenAI, or Anthropic may receive the prompt
            and the relevant excerpts, questions, and answers needed to produce that result. Every
            configured provider can also be used as a failover when another provider fails. Page images
            that Lumina describes for visual understanding are sent only to Google Gemini or to an Ollama
            model; they are not sent to OpenAI or Anthropic. Hosted Lumina creates text embeddings with its
            bundled model inside its own infrastructure, so course text is not sent to an external provider
            for embedding. Optional advertising from Google AdSense or EthicalAds loads only after ad
            consent and then receives browser, device, and network information from your browser.
          </p>
          <p>
            A self-hosted operator chooses the database, storage, mail service, logs, backups, and AI
            providers, and can also choose Ollama for embeddings. With a genuinely local model such as
            Ollama and no external fallback, inference may remain on that operator's infrastructure. Configuring an external key or fallback can
            send relevant content to that provider, so self-hosting alone is not a promise that data
            never leaves the operator's systems.
          </p>
        </>
      ),
    },
    {
      heading: 'Retention and deletion',
      content: (
        <>
          <p>
            Active account records, policy acknowledgements, courses, source files, derived document
            data, generated materials, conversations, quiz history, progress, mastery, and credit ledger
            are kept while the account or related workspace remains active, unless law requires a
            different period. You can delete courses, course documents, profile documents, profile
            knowledge, and conversations yourself. Course and document deletion is permanent: a deletion
            tombstone fences new work while source storage, vectors, and relational rows are purged. A
            failed purge is retried rather than reported as complete.
          </p>
          <p>
            Under the default configuration, verification links are usable for 24 hours, password-reset
            links for 60 minutes, and sign-in sessions for 60 minutes; only hashes of link tokens are
            stored. Used or expired link hashes and the identifiers of signed-out sessions are not
            currently purged on a schedule and remain until the account itself is deleted. Hosted
            operational logs, including any account ID or AI-output excerpt they contain, are retained for
            up to 30 days, and AI usage telemetry for up to 90 days; both periods still run after the
            related course is deleted. Self-hosted operational logs are kept locally for up to 30 days or
            500,000 records by default. Hosted pre-deployment and recovery-drill database snapshots are
            removed after about 30 days, and older S3 source-object versions expire after about 90 days.
            If the hosted database instance itself is retired, its final snapshot is kept until the
            operator deletes it. Deletion from backups occurs as those copies expire and is not
            immediate.
          </p>
          <p>
            You can permanently delete your account from Account → Security after confirming your
            password. Sign-in, existing sessions, verification and password-reset links, and stored
            provider keys stop working immediately. A retrying cleanup process then removes your uploaded
            files, search vectors, courses, generated materials, quizzes, progress, conversations, profile
            data, credit history, and the account record; if a step fails, the deletion stays queued and
            is retried rather than reported as complete. Credit entries on other accounts that you made as
            an administrator are kept for accounting with your name replaced by "Deleted administrator".
            Operational logs and backups expire on the schedules above rather than immediately. A
            self-hosted operator runs the same cleanup and controls its own log and backup retention.
          </p>
        </>
      ),
    },
    {
      heading: 'Your choices and rights',
      content: (
        <p>
          Depending on your location, you may ask to access, correct, export, restrict, object to, or
          delete personal data, withdraw optional consent, or complain to a competent data-protection
          authority. Email {contact} from the account address and describe the request. We may verify
          identity before acting. Some records may be retained where required by law, needed for
          security or legal claims, or present in a bounded backup until it expires. Self-hosted users
          should contact their installation's operator.
        </p>
      ),
    },
    {
      heading: 'Children and international use',
      content: (
        <p>
          Lumina is intended for users aged 16 or older and is not directed at children. Lumina does not
          collect a date of birth or verify age. If you believe a child under 16 has created an account,
          contact {contact} so the account can be closed and its data removed.
        </p>
      ),
    },
    {
      heading: 'Changes',
      content: (
        <p>
          We will update the version and effective date when this notice changes. Material changes will
          be communicated in the service or by email where appropriate. A new Terms agreement will be
          requested when a change materially alters user obligations; privacy consent will be requested
          separately only when consent is the appropriate legal basis.
        </p>
      ),
    },
  ],
};

export const termsOfService: LegalDocument = {
  title: 'Terms of Service',
  summary: 'These terms govern access to hosted Lumina and explain the rules that also matter when using the open-source software.',
  sections: [
    {
      heading: 'Eligibility and accounts',
      content: (
        <p>
          You must be at least 16 years old to use Lumina. If you are below the age at which you can
          enter these terms where you live, a parent or legal guardian must review and agree for you.
          Provide accurate account information, keep credentials private, and notify {contact} of
          suspected compromise. You are responsible for activity under your account unless caused by
          Lumina's failure to use reasonable security.
        </p>
      ),
    },
    {
      heading: 'The service, credits, and changes',
      content: (
        <p>
          Lumina provides AI-assisted study tools. Features, models, limits, and availability may change.
          Hosted accounts may receive configurable credits or usage limits; credits are currently a
          service allowance, are not money, cannot be transferred, and have no cash value. Administrators
          may correct balances. We do not promise uninterrupted or error-free availability.
        </p>
      ),
    },
    {
      heading: 'Your content remains yours',
      content: (
        <>
          <p>
            You retain ownership of content you upload. Uploading does not transfer ownership of source
            material to coDDesigning or Lumina. You grant only the limited, non-exclusive rights needed
            to host, store, copy, extract, OCR, index, embed, retrieve, transform, transmit to a configured
            service provider, and display that content to provide and secure the functions you request.
            This licence ends when the content is deleted, subject to bounded backups and legal retention.
          </p>
          <p>
            You must have permission to upload and process the material, including third-party or
            copyrighted course materials. Do not use Lumina to evade access controls or redistribute
            material unlawfully.
          </p>
        </>
      ),
    },
    {
      heading: 'Generated content and educational use',
      content: (
        <p>
          You may use generated output subject to applicable law, source-material rights, provider terms,
          and institutional rules. AI output can be inaccurate, incomplete, outdated, or misleading.
          Lumina is study assistance, not a human instructor, and does not guarantee grades, exam results,
          mastery, or academic outcomes. Verify important information with authoritative materials and
          instructors. See the <Link to="/legal/ai-disclosure">AI / Educational Disclosure</Link>.
        </p>
      ),
    },
    {
      heading: 'Acceptable use and third parties',
      content: (
        <p>
          You must follow the <Link to="/legal/acceptable-use">Acceptable Use Policy</Link>. AI, email,
          hosting, and advertising providers may apply their own terms. Lumina is not responsible for a
          third-party service outside its reasonable control. Provider routing and data flows are
          described in the <Link to="/legal/privacy">Privacy Notice</Link>.
        </p>
      ),
    },
    {
      heading: 'Suspension, termination, and deletion',
      content: (
        <p>
          We may limit, suspend, or terminate hosted access to protect users or infrastructure, comply
          with law, address non-payment or quota abuse, or respond to a material breach. Where practical,
          we will give notice and an opportunity to cure. On termination, the right to use hosted Lumina
          ends. Content deletion follows the Privacy Notice; provisions that by nature should survive,
          including ownership, disclaimers, liability limits, and dispute terms, remain effective.
        </p>
      ),
    },
    {
      heading: 'Disclaimers and liability',
      content: (
        <p>
          To the maximum extent permitted by law, the service and generated content are provided “as is”
          and “as available,” without implied warranties of accuracy, fitness, merchantability, or
          non-infringement. coDDesigning will not be liable for indirect, incidental, special,
          consequential, or lost-profit damages arising from use of the hosted service. Any aggregate
          liability cap, exclusions, governing law, venue, and mandatory consumer-right language must be
          finalised during legal review and do not override rights that cannot lawfully be excluded.
        </p>
      ),
    },
    {
      heading: 'Contact and changes',
      content: (
        <p>
          Questions may be sent to {contact}. These terms show their effective date and version above.
          Material changes will be announced in the service or by email. Renewed agreement will be sought
          where changes materially alter user obligations or the law requires it.
        </p>
      ),
    },
  ],
};

export const acceptableUsePolicy: LegalDocument = {
  title: 'Acceptable Use Policy',
  summary: 'These rules protect students, rights holders, and the shared infrastructure that runs Lumina.',
  sections: [
    {
      heading: 'Use Lumina lawfully and fairly',
      content: (
        <p>
          Do not use Lumina for unlawful activity, fraud, harassment, threats, impersonation, account
          abuse, or activity that harms other users or shared systems. Do not upload or process material
          in a way that infringes copyright, privacy, confidentiality, or other rights.
        </p>
      ),
    },
    {
      heading: 'Protect the service',
      content: (
        <p>
          Do not upload malware or malicious files; probe, scan, exploit, or bypass security; obtain
          unauthorised access; scrape or automate abusive traffic; disrupt service or cause denial of
          service; evade bans; or bypass credits, quotas, technical controls, or rate limits. Security
          research must follow the <Link to="/legal/security">Responsible Disclosure Policy</Link>.
        </p>
      ),
    },
    {
      heading: 'Academic integrity',
      content: (
        <p>
          AI use is not automatically prohibited. You must follow the rules of your school, university,
          course, instructor, and assessment. Do not misrepresent generated work as your own where that
          is forbidden, use Lumina during a closed assessment, or help another person violate applicable
          academic-integrity rules.
        </p>
      ),
    },
    {
      heading: 'Enforcement',
      content: (
        <p>
          We may investigate and limit or suspend hosted access proportionately to protect the service,
          comply with law, or address a breach. Report abuse privately to {contact}. Self-hosted operators
          are responsible for enforcing rules on their own installations.
        </p>
      ),
    },
  ],
};

export const cookiePolicy: LegalDocument = {
  title: 'Cookie & Browser Storage Policy',
  summary: 'Lumina uses browser storage for sessions and preferences, and loads optional advertising only after consent.',
  sections: [
    {
      heading: 'Strictly necessary storage',
      content: (
        <p>
          Lumina does not currently set a first-party HTTP cookie. It uses localStorage for the bearer
          session token (<code>token</code>), the most recently opened course
          (<code>lumina.activeWorkspaceId</code>), theme preference (<code>lumina.theme</code>), the
          last active tutor or Q&amp;A conversation for each course
          (<code>lumina:course:&lt;id&gt;:conversation:&lt;type&gt;</code>), and your advertising choice
          (<code>lumina_ad_consent</code>). These values keep the user signed in, remember the consent
          decision, and restore requested user interface state. Clearing browser storage signs the user
          out and resets those preferences.
        </p>
      ),
    },
    {
      heading: 'Optional advertising',
      content: (
        <p>
          Hosted Lumina can be configured with Google AdSense or EthicalAds. No advertising script is
          included in the page, and the ad script and ad slots do not load until a signed-in user grants
          ad consent. The choice is stored in localStorage as <code>lumina_ad_consent</code> and can be
          changed from Account → Appearance. An ad provider may then set cookies or similar identifiers and receive browser,
          device, network, and interaction information under its own policy. Lumina has no marketing
          tracker of its own today.
        </p>
      ),
    },
    {
      heading: 'Self-hosted installations and controls',
      content: (
        <p>
          A self-hosted operator controls whether advertising or any additional analytics is enabled and
          must update its notice and consent controls before adding non-essential tracking. You can reject
          optional ads without losing account or study functions. Browser settings can remove all stored
          values, but strictly necessary state will be recreated when the related function is used.
        </p>
      ),
    },
  ],
};

export const aiDisclosure: LegalDocument = {
  title: 'AI / Educational Disclosure',
  summary: 'Lumina is an AI-assisted study tool. Its outputs and learning estimates require human judgment and verification.',
  sections: [
    {
      heading: 'AI-assisted, not a human instructor',
      content: (
        <p>
          Lumina uses AI to generate or transform study guides, flashcards, quiz content, grading feedback,
          explanations, tutor and Q&amp;A responses, topic-mastery estimates, and Exam Mode analyses and
          recommendations. The controls that start this work carry a short AI notice linking to this
          page, and the tutor is labelled as an AI tutor. A conversational presentation does not mean a human teacher is
          answering.
        </p>
      ),
    },
    {
      heading: 'Limitations',
      content: (
        <p>
          AI output may be inaccurate, incomplete, outdated, fabricated, biased, or misleading. A citation
          shows which supplied passage was used; it does not guarantee that the interpretation or answer
          is correct. Generated answers, explanations, grades, mastery signals, and study plans are not
          guaranteed facts. Verify important academic information against official course material and
          instructors. Lumina does not guarantee grades, exam performance, or any academic outcome.
        </p>
      ),
    },
    {
      heading: 'Provider data flow',
      content: (
        <p>
          Relevant prompts, excerpts, questions, and answers may be sent to Gemini, OpenAI, Anthropic,
          or an Ollama server configured by the operator to fulfil a request or fail over after a
          provider error. Page images described for visual understanding go only to Gemini or Ollama. With a genuinely local model such as Ollama and no external fallback, self-hosted
          inference may remain local. Check the <Link to="/legal/privacy">Privacy Notice</Link> before
          submitting sensitive or confidential material.
        </p>
      ),
    },
    {
      heading: 'Responsible academic use',
      content: (
        <p>
          Use AI output as a starting point for learning, not as unquestioned authority. Follow the rules
          of your institution and assessment; see the <Link to="/legal/acceptable-use">Acceptable Use
          Policy</Link>.
        </p>
      ),
    },
  ],
};

export const securityPolicy: LegalDocument = {
  title: 'Security & Responsible Disclosure',
  summary: 'Please report suspected vulnerabilities privately so they can be investigated without putting users at risk.',
  sections: [
    {
      heading: 'How to report',
      content: (
        <p>
          Email {contact} with the subject “Security report”. Do not file a public Jira or GitHub issue
          for an exploitable weakness. Include the affected hosted URL or software version, deployment
          type, reproduction steps, observed impact, relevant logs or screenshots with secrets removed,
          and a safe way to contact you.
        </p>
      ),
    },
    {
      heading: 'Responsible handling',
      content: (
        <p>
          Keep the issue private until a fix or mitigation is available and we have had reasonable time
          to investigate. Avoid privacy violations, data destruction, service disruption, social
          engineering, automated high-volume testing, and accessing more data than needed to demonstrate
          the issue. We will acknowledge useful reports when practical and coordinate remediation and
          disclosure. No legal safe-harbour promise is made by this version of the policy.
        </p>
      ),
    },
    {
      heading: 'Supported scope',
      content: (
        <p>
          Reports may cover the current hosted service and the latest released open-source version.
          Operators of modified or older self-hosted installations remain responsible for their own
          deployment, configuration, updates, and incident response.
        </p>
      ),
    },
  ],
};

export const openSourcePolicy: LegalDocument = {
  title: 'Open Source & Third-Party Notices',
  summary: 'Lumina is distributed under AGPL-3.0-only; bundled components continue under their own licences.',
  sections: [
    {
      heading: 'Lumina licence',
      content: (
        <p>
          Copyright © 2026 coDDesigning contributors. Lumina is free software licensed under the GNU
          Affero General Public License, version 3 only (AGPL-3.0-only). The canonical licence text is
          the repository's <a href="https://github.com/coDDesigning/Lumina/blob/dev/LICENSE" target="_blank" rel="noopener noreferrer">LICENSE file</a>.
          In particular, operators who modify Lumina and let users interact with it over a network must
          offer those users the corresponding source as required by the licence. This summary does not
          replace the licence text.
        </p>
      ),
    },
    {
      heading: 'Third-party components',
      content: (
        <p>
          Libraries, fonts, OCR tools, and model assets retain their own copyrights and licences. The
          maintained inventory and attribution details are in the repository's <a href="https://github.com/coDDesigning/Lumina/blob/dev/THIRD_PARTY_NOTICES.md" target="_blank" rel="noopener noreferrer">Third-Party Notices</a>.
          Those notices must be reviewed whenever distributed dependencies or assets change.
        </p>
      ),
    },
  ],
};
