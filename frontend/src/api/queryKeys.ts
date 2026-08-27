import type { QueryKey } from '@/lib/query/key';

export const queryKeys = {
  courses: (): QueryKey => ['courses'],
  coursesProgress: (): QueryKey => ['courses', 'progress'],

  course: (courseId: number): QueryKey => ['course', courseId],
  courseDocuments: (courseId: number): QueryKey => ['course', courseId, 'documents'],
  courseSettings: (courseId: number): QueryKey => ['course', courseId, 'settings'],
  courseProgress: (courseId: number): QueryKey => ['course', courseId, 'progress'],
  courseOutputs: (courseId: number): QueryKey => ['course', courseId, 'outputs'],
  courseQuizzes: (courseId: number): QueryKey => ['course', courseId, 'quizzes'],
  courseReverseQuizzes: (courseId: number): QueryKey => ['course', courseId, 'reverseQuizzes'],
  courseOutput: (courseId: number, outputId: number): QueryKey => [
    'course',
    courseId,
    'output',
    outputId,
  ],
  courseQuiz: (courseId: number, quizId: number): QueryKey => [
    'course',
    courseId,
    'quiz',
    quizId,
  ],
  courseQuizAttempts: (courseId: number, quizId: number): QueryKey => [
    'course',
    courseId,
    'quiz',
    quizId,
    'attempts',
  ],
  courseQuizAttempt: (courseId: number, quizId: number, attemptId: number): QueryKey => [
    'course',
    courseId,
    'quiz',
    quizId,
    'attempt',
    attemptId,
  ],
  courseConversations: (courseId: number): QueryKey => ['course', courseId, 'conversations'],
  courseConversation: (courseId: number, conversationId: number): QueryKey => [
    'course',
    courseId,
    'conversation',
    conversationId,
  ],

  activityAll: (): QueryKey => ['activity'],
  activity: (limit: number | null): QueryKey => ['activity', limit],
  credits: (userId: number): QueryKey => ['user', userId, 'credits'],
  creditTransactions: (userId: number, limit: number): QueryKey => [
    'user',
    userId,
    'creditTransactions',
    limit,
  ],
  models: (): QueryKey => ['models'],
  profileKnowledge: (): QueryKey => ['profileKnowledge'],

  adminUsers: (): QueryKey => ['admin', 'users'],
  adminUserCourses: (email: string): QueryKey => ['admin', 'users', email, 'courses'],
  adminCosts: (days: number): QueryKey => ['admin', 'costs', days],
  adminCreditLedger: (email: string, limit: number): QueryKey => [
    'admin',
    'creditLedger',
    email,
    limit,
  ],
};
