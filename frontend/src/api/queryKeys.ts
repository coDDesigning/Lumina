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
  courseGenerationJobs: (courseId: number): QueryKey => [
    'course',
    courseId,
    'generationJobs',
  ],
  courseGenerationJob: (courseId: number, jobId: number): QueryKey => [
    'course',
    courseId,
    'generationJob',
    jobId,
  ],
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
  courseQuizSession: (courseId: number, quizId: number, sessionId: number): QueryKey => [
    'course',
    courseId,
    'quiz',
    quizId,
    'session',
    sessionId,
  ],

  // Exam Mode. Every key keeps the ['course', id] prefix so invalidating a
  // course still reaches all of them, and prefix matching is element-wise, so
  // course 1 can never invalidate course 11.
  examSources: (courseId: number): QueryKey => ['course', courseId, 'examSources'],
  examEntitlements: (courseId: number): QueryKey => ['course', courseId, 'examEntitlements'],
  examAnalysis: (courseId: number, analysisId: number | null): QueryKey => [
    'course',
    courseId,
    'examAnalysis',
    analysisId,
  ],
  /** Prefix over every page and filter of one course's extracted questions. */
  examQuestionsAll: (courseId: number): QueryKey => ['course', courseId, 'examQuestions'],
  examQuestions: (
    courseId: number,
    analysisId: number,
    topicKey: string | null,
    limit: number,
    offset: number,
  ): QueryKey => [
    'course',
    courseId,
    'examQuestions',
    analysisId,
    topicKey,
    limit,
    offset,
  ],
  examPlans: (courseId: number): QueryKey => ['course', courseId, 'examPlans'],
  examPlan: (courseId: number, planId: number): QueryKey => [
    'course',
    courseId,
    'examPlan',
    planId,
  ],
  examTopicGuide: (courseId: number, topicKey: string): QueryKey => [
    'course',
    courseId,
    'examTopicGuide',
    topicKey,
  ],
  examTopicSummary: (courseId: number, topicKey: string): QueryKey => [
    'course',
    courseId,
    'examTopicSummary',
    topicKey,
  ],
  examSimilarQuestions: (courseId: number, topicKey: string): QueryKey => [
    'course',
    courseId,
    'examSimilarQuestions',
    topicKey,
  ],
  examMockExam: (courseId: number): QueryKey => ['course', courseId, 'examMockExam'],
  examReviewSheet: (courseId: number): QueryKey => ['course', courseId, 'examReviewSheet'],

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
  userApiKeys: (): QueryKey => ['user', 'apiKeys'],
  profileKnowledge: (): QueryKey => ['profileKnowledge'],
  profileDocuments: (): QueryKey => ['profileDocuments'],
  profileDocument: (id: string): QueryKey => ['profileDocuments', id],

  adminUsers: (): QueryKey => ['admin', 'users'],
  adminUserCourses: (email: string): QueryKey => ['admin', 'users', email, 'courses'],
  adminCosts: (days: number): QueryKey => ['admin', 'costs', days],
  adminCreditLedger: (email: string, limit: number): QueryKey => [
    'admin',
    'creditLedger',
    email,
    limit,
  ],
  adsConfig: (): QueryKey => ['ads', 'config'],
};
