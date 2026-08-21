import { BookOpen, Check, CheckCircle2, FileText, Lightbulb, Sparkles, XCircle } from 'lucide-react';
import type { RetrievedContext, StudyGuideResponse } from '../../api/types';

interface StudyGuideViewProps {
  guide: StudyGuideResponse;
  context?: RetrievedContext | null;
}

/**
 * The read-only rendering of one study guide, shared by fresh generations and
 * the stored history so both always look the same.
 */
export function StudyGuideView({ guide, context = null }: StudyGuideViewProps) {
  return (
    <div className="summary-container">
      <div className="summary-meta-badge">
        <FileText aria-hidden="true" />
        <span>{guide.difficulty.level}</span> • <span>{guide.estimated_study_time}</span>{' '}
        • <span>
          {guide.coverage.status} ({guide.coverage.estimated_completeness}%)
        </span>
      </div>

      {context?.retrieval_narrowed ? (
        <div className="summary-section-card summary-notice" role="note">
          <h4>Focused on the most relevant sections</h4>
          <p>
            Built from the {context.chunks_used} most relevant of{' '}
            {context.chunks_available} content sections in this course.
          </p>
        </div>
      ) : null}

      {context?.context_truncated ? (
        <div className="summary-section-card summary-notice" role="note">
          <h4>Length limit reached</h4>
          <p>
            The relevant material did not all fit in one request, so the least
            relevant sections were left out.
          </p>
        </div>
      ) : null}

      <div className="summary-section-card">
        <h4>
          <BookOpen aria-hidden="true" />
          {guide.title}
        </h4>
        <div className="summary-body-text">{guide.summary}</div>
        <p className="summary-hint">{guide.difficulty.reason}</p>
      </div>

      {guide.learning_objectives.length > 0 ? (
        <div className="summary-section-card">
          <h4>
            <Check aria-hidden="true" />
            Learning Objectives
          </h4>
          <ul className="summary-bullet-list">
            {guide.learning_objectives.map((objective, index) => (
              <li key={index}>{objective}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {guide.key_points.length > 0 ? (
        <div className="summary-section-card">
          <h4>
            <Check aria-hidden="true" />
            Key Points
          </h4>
          <ul className="summary-bullet-list">
            {guide.key_points.map((point, index) => (
              <li key={index}>{point}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {guide.important_terms.length > 0 ? (
        <div className="summary-section-card">
          <h4>
            <Lightbulb aria-hidden="true" />
            Important Terms
          </h4>
          <div className="definitions-grid">
            {guide.important_terms.map((term, index) => (
              <div className="definition-item" key={index}>
                <strong>{term.term}</strong>
                <p>{term.definition}</p>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {guide.common_mistakes.length > 0 ? (
        <div className="summary-section-card">
          <h4>
            <XCircle aria-hidden="true" />
            Common Mistakes
          </h4>
          <ul className="summary-mistake-list">
            {guide.common_mistakes.map((item, index) => (
              <li key={index}>
                <span className="mistake-line">
                  <XCircle aria-hidden="true" />
                  {item.mistake}
                </span>
                <span className="correction-line">
                  <CheckCircle2 aria-hidden="true" />
                  {item.correction}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {guide.exam_tips.lecture_based.length > 0 ||
      guide.exam_tips.ai_suggestions.length > 0 ? (
        <div className="summary-section-card">
          <h4>
            <Sparkles aria-hidden="true" />
            Exam Tips
          </h4>
          {guide.exam_tips.lecture_based.length > 0 ? (
            <>
              <h5>From your course material</h5>
              <ul className="summary-bullet-list">
                {guide.exam_tips.lecture_based.map((tip, index) => (
                  <li key={index}>{tip}</li>
                ))}
              </ul>
            </>
          ) : null}
          {guide.exam_tips.ai_suggestions.length > 0 ? (
            <>
              <h5>AI suggestions</h5>
              <ul className="summary-bullet-list">
                {guide.exam_tips.ai_suggestions.map((tip, index) => (
                  <li key={index}>{tip}</li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      ) : null}

      {guide.prerequisites.length > 0 ? (
        <div className="summary-section-card">
          <h4>
            <BookOpen aria-hidden="true" />
            Prerequisites
          </h4>
          <ul className="summary-bullet-list">
            {guide.prerequisites.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {guide.confidence_notes ? (
        <p className="summary-hint">{guide.confidence_notes}</p>
      ) : null}
    </div>
  );
}
