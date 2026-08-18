import React from 'react';
import {
  Award,
  BarChart3,
  BookCheck,
  CheckCircle,
  TrendingUp,
} from 'lucide-react';
import { QuizResult, calculateTopicMastery } from '../../data/mockStudyData';
import './study.css';

interface ProgressDashboardProps {
  courseName: string;
  topics: string[];
  documentCount: number;
  quizHistory: QuizResult[];
  onOpenQuizModal?: () => void;
  onOpenSummaryModal?: () => void;
}

export const ProgressDashboard: React.FC<ProgressDashboardProps> = ({
  courseName,
  topics,
  documentCount,
  quizHistory,
  onOpenQuizModal,
  onOpenSummaryModal,
}) => {
  const topicMastery = calculateTopicMastery(topics, quizHistory);

  const averageScore =
    quizHistory.length > 0
      ? Math.round(
          quizHistory.reduce((acc, q) => acc + q.scorePercentage, 0) /
            quizHistory.length
        )
      : 74;

  const totalQuizzes = Math.max(quizHistory.length, 1);

  // Overall readiness score
  const readinessScore = Math.min(
    100,
    Math.round(
      averageScore * 0.5 +
        Math.min(100, documentCount * 25) * 0.3 +
        (topicMastery.filter((t) => t.status === 'Mastered').length /
          Math.max(1, topicMastery.length)) *
          100 *
          0.2
    )
  );

  return (
    <div className="progress-dashboard-card">
      <header className="progress-header">
        <div>
          <h3>
            <BarChart3 style={{ color: '#8b5cf6', width: '20px', height: '20px' }} />
            {courseName} Learning Analytics
          </h3>
          <p style={{ margin: '2px 0 0', fontSize: '12px', color: '#716b78' }}>
            Real-time conceptual readiness and quiz performance tracking
          </p>
        </div>
        <span
          style={{
            fontSize: '12px',
            fontWeight: 700,
            color: '#7e22ce',
            background: '#f3e8ff',
            padding: '4px 10px',
            borderRadius: '20px',
          }}
        >
          {readinessScore}% Exam Readiness
        </span>
      </header>

      <div className="progress-metrics-grid">
        <div className="metric-box">
          <strong>{readinessScore}%</strong>
          <span>Readiness Score</span>
        </div>
        <div className="metric-box">
          <strong>{totalQuizzes}</strong>
          <span>Quizzes Completed</span>
        </div>
        <div className="metric-box">
          <strong>{averageScore}%</strong>
          <span>Average Quiz Score</span>
        </div>
        <div className="metric-box">
          <strong>{documentCount}</strong>
          <span>Sources Processed</span>
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            fontSize: '14px',
            fontWeight: 700,
            color: '#25113f',
          }}
        >
          <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <TrendingUp style={{ width: '16px', height: '16px', color: '#8b5cf6' }} />
            Topic Mastery Matrix
          </span>
          <span style={{ fontSize: '12px', color: '#716b78', fontWeight: 500 }}>
            {topicMastery.filter((t) => t.status === 'Mastered').length} of{' '}
            {topicMastery.length} Mastered
          </span>
        </div>

        <div className="topics-mastery-list">
          {topicMastery.map((item) => {
            const badgeClass =
              item.status === 'Mastered'
                ? 'mastered'
                : item.status === 'In Progress'
                ? 'in-progress'
                : 'needs-review';

            return (
              <div className="topic-mastery-row" key={item.topic}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <CheckCircle
                    style={{
                      width: '15px',
                      height: '15px',
                      color:
                        item.status === 'Mastered'
                          ? '#10b981'
                          : item.status === 'In Progress'
                          ? '#6366f1'
                          : '#ef4444',
                    }}
                  />
                  <span className="topic-mastery-name">{item.topic}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <span style={{ fontSize: '12px', fontWeight: 700, color: '#4b4652' }}>
                    {item.masteryPercentage}%
                  </span>
                  <span className={`topic-badge ${badgeClass}`}>{item.status}</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div
        style={{
          display: 'flex',
          gap: '10px',
          paddingTop: '6px',
          borderTop: '1px solid #f3ecfa',
        }}
      >
        {onOpenSummaryModal && (
          <button
            type="button"
            className="secondary-button"
            style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px' }}
            onClick={onOpenSummaryModal}
          >
            <BookCheck style={{ width: '15px', height: '15px' }} />
            Generate Summary
          </button>
        )}
        {onOpenQuizModal && (
          <button
            type="button"
            className="primary-button"
            style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px' }}
            onClick={onOpenQuizModal}
          >
            <Award style={{ width: '15px', height: '15px' }} />
            Start Practice Quiz
          </button>
        )}
      </div>
    </div>
  );
};
