import { BarChart3, BookOpen, Sparkles, Target, TrendingUp } from 'lucide-react';
import type { CourseProgressResponse, MasteryStatus } from '../../api/types';
import './study.css';

interface ProgressDashboardProps {
  courseName: string;
  documentCount: number;
  readyDocumentCount: number;
  progress: CourseProgressResponse | null;
  isLoading: boolean;
  error: string | null;
  onOpenQuizModal?: () => void;
  onOpenSummaryModal?: () => void;
}

const STATUS_CLASSES: Record<MasteryStatus, string> = {
  Mastered: 'mastered',
  'In Progress': 'in-progress',
  'Needs Review': 'needs-review',
};

function statusClass(status: string): string {
  return STATUS_CLASSES[status as MasteryStatus] ?? 'in-progress';
}

export function ProgressDashboard({
  courseName,
  documentCount,
  readyDocumentCount,
  progress,
  isLoading,
  error,
  onOpenQuizModal,
  onOpenSummaryModal,
}: ProgressDashboardProps) {
  const attemptsCount = progress?.attempts_count ?? 0;
  const topicMastery = progress?.topic_mastery ?? [];
  const averagePercentage =
    progress?.average_score != null ? Math.round(progress.average_score * 100) : null;

  return (
    <div className="progress-dashboard-card">
      <div className="progress-header">
        <h3>
          <BarChart3 aria-hidden="true" />
          {courseName} Learning Analytics
        </h3>
      </div>

      {error ? (
        <div className="summary-section-card summary-notice is-danger" role="alert">
          <p>{error}</p>
        </div>
      ) : null}

      <div className="progress-metrics-grid">
        <div className="metric-box">
          <strong>{isLoading ? '—' : attemptsCount}</strong>
          <span>Quizzes Completed</span>
        </div>
        <div className="metric-box">
          <strong>
            {isLoading ? '—' : averagePercentage !== null ? `${averagePercentage}%` : '—'}
          </strong>
          <span>Average Quiz Score</span>
        </div>
        <div className="metric-box">
          <strong>{readyDocumentCount}</strong>
          <span>Sources Processed</span>
        </div>
        <div className="metric-box">
          <strong>{documentCount}</strong>
          <span>Sources Added</span>
        </div>
      </div>

      <div className="summary-section-card">
        <h4>
          <Target aria-hidden="true" />
          Topic Mastery
        </h4>

        {isLoading ? (
          <p className="summary-hint">Loading your progress…</p>
        ) : topicMastery.length === 0 ? (
          <p className="summary-empty-state">
            Take a practice quiz to start tracking which topics you have mastered.
          </p>
        ) : (
          <div className="topics-mastery-list">
            {topicMastery.map((topic) => (
              <div className="topic-mastery-row" key={topic.topic}>
                <span className="topic-mastery-name">{topic.topic}</span>
                <span className="topic-mastery-count">
                  {topic.questions_correct}/{topic.questions_answered}
                </span>
                <span className={`topic-badge ${statusClass(topic.status)}`}>
                  {topic.status} · {topic.mastery_percentage}%
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="progress-actions">
        <button className="secondary-button" type="button" onClick={onOpenSummaryModal}>
          <BookOpen aria-hidden="true" />
          Generate Summary
        </button>
        <button className="primary-button" type="button" onClick={onOpenQuizModal}>
          <Sparkles aria-hidden="true" />
          Start Practice Quiz
        </button>
      </div>

      {topicMastery.length > 0 ? (
        <p className="summary-hint">
          <TrendingUp aria-hidden="true" /> Mastery is measured from the questions you have
          actually answered in this course.
        </p>
      ) : null}
    </div>
  );
}
