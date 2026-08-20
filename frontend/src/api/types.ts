export type LooseUnion<T extends string> = T | (string & Record<never, never>);

export interface BaseResponse<T> {
  success: boolean;
  message: string;
  data: T | null;
}

export interface User {
  id: number;
  name: string;
  email: string;
  role: string;
  is_banned: boolean;
  credits: number | null;
  preferred_model: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
}

export interface Course {
  id: number;
  title: string;
  description: string | null;
  owner_id: number;
  created_at: string;
  updated_at: string;
  semester: string | null;
  exam_date: string | null;
  syllabus: string | null;
  topics: string | null;
}

export interface CourseCreate {
  title: string;
  description?: string;
  semester?: string;
  exam_date?: string;
  syllabus?: string;
  topics?: string;
}

export interface CourseUpdate {
  title?: string;
  description?: string;
  semester?: string;
  exam_date?: string;
  syllabus?: string;
  topics?: string;
}

export type DocumentStatus =
  | 'uploaded'
  | 'processing'
  | 'ready'
  | 'failed'
  | 'deleting';

export type ProcessingJobStatus = 'queued' | 'running' | 'succeeded' | 'failed';

export type ProcessingStage =
  | 'validating'
  | 'extracting_text'
  | 'running_ocr'
  | 'understanding_images'
  | 'cleaning_text'
  | 'chunking'
  | 'generating_embeddings';

export interface DocumentResponse {
  id: string;
  original_file_name: string;
  file_type: string;
  mime_type: string;
  file_size: number;
  course_id: number;
  status: LooseUnion<DocumentStatus>;
  created_at: string;
  updated_at: string;
}

export interface DocumentUploadResponse {
  document: DocumentResponse;
  duplicate: boolean;
}

export interface ProcessingJobResponse {
  id: number;
  status: LooseUnion<ProcessingJobStatus>;
  attempt_count: number;
  max_attempts: number;
  available_at: string;
  started_at: string | null;
  finished_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  processing_stage: LooseUnion<ProcessingStage> | null;
  failed_stage: LooseUnion<ProcessingStage> | null;
}

export interface DocumentStatusResponse {
  document: DocumentResponse;
  processing_job: ProcessingJobResponse;
}

export interface BoundedContext {
  context_truncated: boolean;
  chunks_used: number;
  chunks_available: number;
}

/**
 * Reporting for material chosen by semantic retrieval.
 *
 * `context_truncated` means the character budget dropped a chunk retrieval had
 * already selected. Retrieval returning a subset of the course is the normal
 * case rather than a truncation, so `retrieval_narrowed` carries that instead.
 */
export interface RetrievedContext extends BoundedContext {
  retrieval_narrowed: boolean;
  lowest_similarity: number | null;
  highest_similarity: number | null;
}

export type SummaryFormat =
  | 'overview'
  | 'comprehensive'
  | 'key_concepts'
  | 'exam_tips';

export type SummaryLength = 'short' | 'medium' | 'long';

export type DetailLevel = 'basic' | 'standard' | 'detailed';

export type SummaryMode = 'general' | 'exam_focused';

export interface StudyGuideRequest {
  summary_format: SummaryFormat;
  topic_focus: string;
  summary_length?: SummaryLength;
  detail_level?: DetailLevel;
  summary_mode?: SummaryMode;
}

export interface ImportantTerm {
  term: string;
  definition: string;
}

export interface CommonMistake {
  mistake: string;
  correction: string;
}

export interface ExamTips {
  lecture_based: string[];
  ai_suggestions: string[];
}

export type DifficultyLevel = 'Easy' | 'Medium' | 'Hard';

export interface Difficulty {
  level: LooseUnion<DifficultyLevel>;
  reason: string;
}

export type CoverageStatus =
  | 'Complete'
  | 'Mostly Complete'
  | 'Partial'
  | 'Limited';

export interface Coverage {
  status: LooseUnion<CoverageStatus>;
  estimated_completeness: number;
}

export interface StudyGuideResponse {
  title: string;
  summary: string;
  key_points: string[];
  important_terms: ImportantTerm[];
  common_mistakes: CommonMistake[];
  exam_tips: ExamTips;
  difficulty: Difficulty;
  estimated_study_time: string;
  prerequisites: string[];
  learning_objectives: string[];
  coverage: Coverage;
  confidence_notes: string;
}

export interface StudyGuideGenerationResult extends RetrievedContext {
  study_guide: StudyGuideResponse;
  generated_output_id: number;
}

export interface GenerationSettings {
  version: number;
  output_type: string;
  summary_format?: SummaryFormat;
  topic_focus?: string;
  summary_length?: SummaryLength;
  detail_level?: DetailLevel;
  summary_mode?: SummaryMode;
  retrieval_limit?: number;
  retrieval_min_similarity?: number;
}

export interface GenerationContext {
  version: number;
  chunks_ranked: number;
  chunks_retrieved: number;
  chunks_used: number;
  chunks_available: number;
  lowest_similarity: number | null;
  highest_similarity: number | null;
  truncated: boolean;
}

export interface GeneratedOutputSummary {
  id: number;
  course_id: number;
  output_type: string;
  user_id: number | null;
  model_used: string | null;
  created_at: string;
  generation_settings: GenerationSettings | null;
  generation_context: GenerationContext | null;
}

/**
 * `content` is deliberately loose: one stored row whose JSON no longer matches
 * its feature schema must still render rather than break the whole history.
 */
export interface GeneratedOutputDetail extends GeneratedOutputSummary {
  content: Record<string, unknown> | string;
}

export type QuizQuestionType = 'multiple_choice' | 'true_false';

export type QuizDifficulty = 'easy' | 'medium' | 'hard';

export interface QuizRequest {
  question_count: number;
  question_type: QuizQuestionType;
  difficulty: QuizDifficulty;
  topic_focus: string;
}

export interface QuizQuestionView {
  question_id: number;
  question_number: number;
  topic: string;
  question: string;
  options: string[];
  correct_option_index: number;
  explanation: string;
}

export interface QuizView {
  quiz_id: number;
  title: string;
  questions: QuizQuestionView[];
}

export interface QuizGenerationResult extends BoundedContext {
  quiz: QuizView;
}

export interface QuizAnswerSubmission {
  question_id: number;
  selected_option_index: number | null;
}

export interface QuizAttemptRequest {
  answers: QuizAnswerSubmission[];
  time_spent_seconds?: number | null;
}

export interface QuizAnswerResult {
  question_id: number;
  selected_option_index: number | null;
  correct_option_index: number;
  is_correct: boolean;
}

export interface QuizAttemptResponse {
  attempt_id: number;
  quiz_id: number;
  score: number;
  correct_count: number;
  total_questions: number;
  time_spent_seconds: number | null;
  created_at: string;
  answers: QuizAnswerResult[];
}

export type MasteryStatus = 'Mastered' | 'In Progress' | 'Needs Review';

export interface TopicMastery {
  topic: string;
  questions_answered: number;
  questions_correct: number;
  mastery_percentage: number;
  status: LooseUnion<MasteryStatus>;
}

export interface CourseProgressResponse {
  attempts_count: number;
  average_score: number | null;
  topic_mastery: TopicMastery[];
}

export interface ProfileKnowledgeItem {
  id: number;
  user_id: number;
  topic: string;
  detail: string;
  created_at: string;
  updated_at: string;
}

export interface ProfileKnowledgeCreate {
  topic: string;
  detail: string;
}

export interface ProfileKnowledgeUpdate {
  topic?: string;
  detail?: string;
}

export interface ProfileKnowledgeImport {
  items: ProfileKnowledgeCreate[];
}

export interface CourseQARequest {
  question: string;
}

export interface CourseQAGenerationResult extends BoundedContext {
  answer: string;
}

