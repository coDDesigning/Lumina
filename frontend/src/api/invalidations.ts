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
