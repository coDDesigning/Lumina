import { apiClient, unwrapData } from './client';
import { parseExamTopicGuideDocument } from './examTopicGuideDocument';
import type {
  BaseResponse,
  ExamAnalysisRequest,
  ExamAnalysisResult,
  ExamAnalysisView,
  ExamEntitlementView,
  ExamMockExamRequest,
  ExamMockExamResult,
  ExamPlanArtifactRequest,
  ExamPlanList,
  ExamPlanRequest,
  ExamPlanView,
  ExamQuestionPage,
  ExamReviewSheetDocument,
  ExamReviewSheetResult,
  ExamSimilarQuestionsResult,
  ExamSourceInventory,
  ExamTopicArtifactRequest,
  ExamTopicGuideDocument,
  ExamTopicGuideResult,
  ExamTopicQuizRequest,
  ExamTopicQuizResult,
  ExamTopicSummaryDocument,
  ExamTopicSummaryResult,
  QuizView,
  SimilarQuestionRequest,
} from './types';

/**
 * A topic key is student-derived text that reaches a URL path, so it is encoded
 * at the single place that builds one rather than trusted at each call site.
 */
function topicPath(courseId: number, topicKey: string, suffix: string): string {
  return `/courses/${courseId}/exam-mode/topics/${encodeURIComponent(topicKey)}/${suffix}`;
}

function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) {
    if (value !== null && value !== undefined) {
      search.set(name, String(value));
    }
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : '';
}

export interface ExamQuestionQuery {
  topicKey?: string | null;
  limit?: number;
  offset?: number;
}

export const examModeAPI = {
  listSources: async (
    courseId: number,
    options?: RequestInit,
  ): Promise<ExamSourceInventory> => {
    const res = await apiClient.get<BaseResponse<ExamSourceInventory>>(
      `/courses/${courseId}/exam-mode/sources`,
      options,
    );
    return unwrapData(res, 'Exam sources');
  },

  /**
   * Which topics this student has already unlocked in this course.
   *
   * Owner-scoped on the server, so it must not be read in administrator
   * support mode -- what somebody bought is theirs.
   */
  listEntitlements: async (
    courseId: number,
    options?: RequestInit,
  ): Promise<ExamEntitlementView> => {
    const res = await apiClient.get<BaseResponse<ExamEntitlementView>>(
      `/courses/${courseId}/exam-mode/entitlements`,
      options,
    );
    return unwrapData(res, 'Exam topic entitlements');
  },

  analyse: async (
    courseId: number,
    request: ExamAnalysisRequest,
    options?: RequestInit,
  ): Promise<ExamAnalysisResult> => {
    const res = await apiClient.post<BaseResponse<ExamAnalysisResult>>(
      `/courses/${courseId}/exam-mode/analysis`,
      request,
      options,
    );
    return unwrapData(res, 'Exam source analysis');
  },

  /** A rescan is its own operation with its own price; it is not a retry. */
  rescan: async (
    courseId: number,
    request: ExamAnalysisRequest,
    options?: RequestInit,
  ): Promise<ExamAnalysisResult> => {
    const res = await apiClient.post<BaseResponse<ExamAnalysisResult>>(
      `/courses/${courseId}/exam-mode/analysis/rescan`,
      request,
      options,
    );
    return unwrapData(res, 'Exam source rescan');
  },

  getAnalysis: async (
    courseId: number,
    analysisId?: number | null,
    options?: RequestInit,
  ): Promise<ExamAnalysisView> => {
    const res = await apiClient.get<BaseResponse<ExamAnalysisView>>(
      `/courses/${courseId}/exam-mode/analysis${query({ output_id: analysisId })}`,
      options,
    );
    return unwrapData(res, 'Exam source analysis');
  },

  listQuestions: async (
    courseId: number,
    analysisId: number,
    { topicKey, limit, offset }: ExamQuestionQuery = {},
    options?: RequestInit,
  ): Promise<ExamQuestionPage> => {
    const res = await apiClient.get<BaseResponse<ExamQuestionPage>>(
      `/courses/${courseId}/exam-mode/analysis/${analysisId}/questions` +
        query({ topic_key: topicKey, limit, offset }),
      options,
    );
    return unwrapData(res, 'Past exam questions');
  },

  createPlan: async (
    courseId: number,
    request: ExamPlanRequest,
    options?: RequestInit,
  ): Promise<ExamPlanView> => {
    const res = await apiClient.post<BaseResponse<ExamPlanView>>(
      `/courses/${courseId}/exam-mode/plans`,
      request,
      options,
    );
    return unwrapData(res, 'Exam plan');
  },

  listPlans: async (courseId: number, options?: RequestInit): Promise<ExamPlanList> => {
    const res = await apiClient.get<BaseResponse<ExamPlanList>>(
      `/courses/${courseId}/exam-mode/plans`,
      options,
    );
    return unwrapData(res, 'Exam plans');
  },

  /** A stored read: no provider, no retrieval, no charge, and no write. */
  getPlan: async (
    courseId: number,
    planId: number,
    options?: RequestInit,
  ): Promise<ExamPlanView> => {
    const res = await apiClient.get<BaseResponse<ExamPlanView>>(
      `/courses/${courseId}/exam-mode/plans/${planId}`,
      options,
    );
    return unwrapData(res, 'Exam plan');
  },

  generateTopicGuide: async (
    courseId: number,
    topicKey: string,
    request: ExamTopicArtifactRequest,
    options?: RequestInit,
  ): Promise<ExamTopicGuideResult> => {
    const res = await apiClient.post<BaseResponse<ExamTopicGuideResult>>(
      topicPath(courseId, topicKey, 'guide'),
      request,
      options,
    );
    return unwrapData(res, 'Exam topic guide');
  },

  getTopicGuide: async (
    courseId: number,
    topicKey: string,
    options?: RequestInit,
  ): Promise<ExamTopicGuideDocument> => {
    const res = await apiClient.get<BaseResponse<unknown>>(
      topicPath(courseId, topicKey, 'guide'),
      options,
    );
    return parseExamTopicGuideDocument(unwrapData(res, 'Exam topic guide'));
  },

  generateTopicSummary: async (
    courseId: number,
    topicKey: string,
    request: ExamTopicArtifactRequest,
    options?: RequestInit,
  ): Promise<ExamTopicSummaryResult> => {
    const res = await apiClient.post<BaseResponse<ExamTopicSummaryResult>>(
      topicPath(courseId, topicKey, 'summary'),
      request,
      options,
    );
    return unwrapData(res, 'Exam topic summary');
  },

  getTopicSummary: async (
    courseId: number,
    topicKey: string,
    options?: RequestInit,
  ): Promise<ExamTopicSummaryDocument> => {
    const res = await apiClient.get<BaseResponse<ExamTopicSummaryDocument>>(
      topicPath(courseId, topicKey, 'summary'),
      options,
    );
    return unwrapData(res, 'Exam topic summary');
  },

  generateTopicPractice: async (
    courseId: number,
    topicKey: string,
    request: ExamTopicQuizRequest,
    options?: RequestInit,
  ): Promise<ExamTopicQuizResult> => {
    const res = await apiClient.post<BaseResponse<ExamTopicQuizResult>>(
      topicPath(courseId, topicKey, 'practice'),
      request,
      options,
    );
    return unwrapData(res, 'Exam topic practice');
  },

  /** Same shape as practice, but served with its answers withheld. */
  generateTopicExam: async (
    courseId: number,
    topicKey: string,
    request: ExamTopicQuizRequest,
    options?: RequestInit,
  ): Promise<ExamTopicQuizResult> => {
    const res = await apiClient.post<BaseResponse<ExamTopicQuizResult>>(
      topicPath(courseId, topicKey, 'exam'),
      request,
      options,
    );
    return unwrapData(res, 'Exam topic exam');
  },

  generateSimilarQuestions: async (
    courseId: number,
    topicKey: string,
    request: SimilarQuestionRequest,
    options?: RequestInit,
  ): Promise<ExamSimilarQuestionsResult> => {
    const res = await apiClient.post<BaseResponse<ExamSimilarQuestionsResult>>(
      topicPath(courseId, topicKey, 'similar-questions'),
      request,
      options,
    );
    return unwrapData(res, 'Similar questions');
  },

  getSimilarQuestions: async (
    courseId: number,
    topicKey: string,
    options?: RequestInit,
  ): Promise<QuizView> => {
    const res = await apiClient.get<BaseResponse<QuizView>>(
      topicPath(courseId, topicKey, 'similar-questions'),
      options,
    );
    return unwrapData(res, 'Similar questions');
  },

  generateMockExam: async (
    courseId: number,
    request: ExamMockExamRequest,
    options?: RequestInit,
  ): Promise<ExamMockExamResult> => {
    const res = await apiClient.post<BaseResponse<ExamMockExamResult>>(
      `/courses/${courseId}/exam-mode/mock-exam`,
      request,
      options,
    );
    return unwrapData(res, 'Mock exam');
  },

  getMockExam: async (courseId: number, options?: RequestInit): Promise<QuizView> => {
    const res = await apiClient.get<BaseResponse<QuizView>>(
      `/courses/${courseId}/exam-mode/mock-exam`,
      options,
    );
    return unwrapData(res, 'Mock exam');
  },

  generateReviewSheet: async (
    courseId: number,
    request: ExamPlanArtifactRequest,
    options?: RequestInit,
  ): Promise<ExamReviewSheetResult> => {
    const res = await apiClient.post<BaseResponse<ExamReviewSheetResult>>(
      `/courses/${courseId}/exam-mode/review-sheet`,
      request,
      options,
    );
    return unwrapData(res, 'Review sheet');
  },

  getReviewSheet: async (
    courseId: number,
    options?: RequestInit,
  ): Promise<ExamReviewSheetDocument> => {
    const res = await apiClient.get<BaseResponse<ExamReviewSheetDocument>>(
      `/courses/${courseId}/exam-mode/review-sheet`,
      options,
    );
    return unwrapData(res, 'Review sheet');
  },
};
