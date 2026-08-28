import { queryCache } from '@/lib/query/cache';
import { queryKeys } from './queryKeys';

export function afterDocumentChanged(courseId: number): void {
  void queryCache.invalidate(queryKeys.courseDocuments(courseId));
  void queryCache.invalidate(queryKeys.courseProgress(courseId));
  void queryCache.invalidate(queryKeys.coursesProgress());
}

export function afterStudyGuideGenerated(courseId: number): void {
  void queryCache.invalidate(queryKeys.courseOutputs(courseId));
  void queryCache.invalidate(queryKeys.courseProgress(courseId));
  void queryCache.invalidate(queryKeys.coursesProgress());
  void queryCache.invalidate(queryKeys.activityAll());
}

export function afterFlashcardsGenerated(courseId: number): void {
  void queryCache.invalidate(queryKeys.courseOutputs(courseId));
  void queryCache.invalidate(queryKeys.activityAll());
}

export function afterQuizGenerated(courseId: number): void {
  void queryCache.invalidate(queryKeys.courseOutputs(courseId));
  void queryCache.invalidate(queryKeys.courseQuizzes(courseId));
  void queryCache.invalidate(queryKeys.courseProgress(courseId));
  void queryCache.invalidate(queryKeys.coursesProgress());
  void queryCache.invalidate(queryKeys.activityAll());
}

export function afterQuizAttempt(courseId: number): void {
  void queryCache.invalidate(queryKeys.courseProgress(courseId));
  void queryCache.invalidate(queryKeys.coursesProgress());
  void queryCache.invalidate(queryKeys.courseQuizzes(courseId));
  void queryCache.invalidate(queryKeys.courseOutputs(courseId));
  void queryCache.invalidate(queryKeys.activityAll());
}

export function afterConversationTurn(courseId: number): void {
  void queryCache.invalidate(queryKeys.courseConversations(courseId));
  void queryCache.invalidate(queryKeys.activityAll());
}

export function afterCourseCreated(): void {
  void queryCache.invalidate(queryKeys.coursesProgress());
}

export function afterCourseUpdated(courseId: number): void {
  void queryCache.invalidate(queryKeys.course(courseId));
}

export function afterCourseDeleted(courseId: number): void {
  queryCache.remove(queryKeys.course(courseId));
  void queryCache.invalidate(queryKeys.coursesProgress());
  void queryCache.invalidate(queryKeys.activityAll());
}

export function afterExamRoadmapGenerated(courseId: number): void {
  void queryCache.invalidate(queryKeys.courseOutputs(courseId));
  void queryCache.invalidate(queryKeys.activityAll());
}

// Exam Mode. Each helper invalidates its own prefixes plus only the shared
// consumers that genuinely changed, because a broader sweep would refetch
// screens the operation did not touch.

/** A fresh analysis supersedes the latest one and may promote course topics. */
export function afterExamAnalysis(courseId: number): void {
  void queryCache.invalidate(queryKeys.examAnalysis(courseId, null));
  void queryCache.invalidate(queryKeys.examSources(courseId));
  void queryCache.invalidate(queryKeys.course(courseId));
  void queryCache.invalidate(queryKeys.courseOutputs(courseId));
  void queryCache.invalidate(queryKeys.activityAll());
}

/** A rescan is an analysis that also re-reads papers, so it clears questions. */
export function afterExamRescan(courseId: number): void {
  afterExamAnalysis(courseId);
  void queryCache.invalidate(queryKeys.examQuestionsAll(courseId));
}

export function afterExamPlanCreated(courseId: number): void {
  void queryCache.invalidate(queryKeys.examPlans(courseId));
  void queryCache.invalidate(queryKeys.courseOutputs(courseId));
  void queryCache.invalidate(queryKeys.activityAll());
}

/**
 * A topic artifact may have bought the topic's unlock, so the entitlement set
 * and the balance both move. Earlier plan versions are untouched and are
 * deliberately not invalidated.
 */
export function afterExamTopicArtifact(courseId: number, topicKey: string): void {
  void queryCache.invalidate(queryKeys.examTopicGuide(courseId, topicKey));
  void queryCache.invalidate(queryKeys.examTopicSummary(courseId, topicKey));
  void queryCache.invalidate(queryKeys.examEntitlements(courseId));
  void queryCache.invalidate(queryKeys.courseOutputs(courseId));
  void queryCache.invalidate(queryKeys.activityAll());
}

export function afterExamTopicQuiz(courseId: number, topicKey: string): void {
  afterExamTopicArtifact(courseId, topicKey);
  void queryCache.invalidate(queryKeys.courseQuizzes(courseId));
}

export function afterExamSimilarQuestions(courseId: number, topicKey: string): void {
  void queryCache.invalidate(queryKeys.examSimilarQuestions(courseId, topicKey));
  afterExamTopicQuiz(courseId, topicKey);
}

export function afterExamMockExam(courseId: number): void {
  void queryCache.invalidate(queryKeys.examMockExam(courseId));
  void queryCache.invalidate(queryKeys.courseQuizzes(courseId));
  void queryCache.invalidate(queryKeys.courseOutputs(courseId));
  void queryCache.invalidate(queryKeys.activityAll());
}

export function afterExamReviewSheet(courseId: number): void {
  void queryCache.invalidate(queryKeys.examReviewSheet(courseId));
  void queryCache.invalidate(queryKeys.courseOutputs(courseId));
  void queryCache.invalidate(queryKeys.activityAll());
}

/**
 * A finished sitting is an ordinary attempt, so it moves everything an attempt
 * moves -- and it also settles the sitting itself, which a reopen would
 * otherwise read as still active.
 */
export function afterTimedSessionSubmitted(
  courseId: number,
  quizId: number,
  sessionId: number,
): void {
  void queryCache.invalidate(queryKeys.courseQuizSession(courseId, quizId, sessionId));
  void queryCache.invalidate(queryKeys.courseQuizAttempts(courseId, quizId));
  afterQuizAttempt(courseId);
}
