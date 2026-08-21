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
  education_level: EducationLevel;
}

export type CreditReason =
  | 'initial_grant'
  | 'periodic_grant'
  | 'generation_charge'
  | 'generation_refund'
  | 'admin_grant'
  | 'support_compensation'
  | 'admin_adjustment'
  | 'metering_reset'
  | 'migration_reconciliation';

export type AdminCreditReason = Extract<
  CreditReason,
  'admin_grant' | 'support_compensation' | 'admin_adjustment'
>;

export type CreditActorType = 'system' | 'user' | 'admin' | 'migration';

export interface CreditTransaction {
  id: number;
  delta: number;
  balance_after: number;
  reason: LooseUnion<CreditReason>;
  actor_type: LooseUnion<CreditActorType>;
  actor_user_id: number | null;
  actor_label: string | null;
  source_type: string | null;
  source_id: number | null;
  refunds_transaction_id: number | null;
  grant_period: string | null;
  note: string | null;
  created_at: string;
}

export interface CreditMutation {
  user: User;
  transaction: CreditTransaction;
}

/** The credit-charging features, keyed as the backend names them. */
export type CreditSource =
  | 'study_guide'
  | 'quiz'
  | 'quiz_open_ended'
  | 'flashcard'
  | 'ai_tutor'
  | 'course_qa'
  | 'prompt_generator';

export interface CreditStatus {
  /** null means this account is not metered, so no credit UI applies. */
  credits: number | null;
  metering_enabled: boolean;
  monthly_grant: number | null;
  balance_cap: number | null;
  next_grant_at: string | null;
  generation_costs: Partial<Record<LooseUnion<CreditSource>, number>>;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
}

export type EducationLevel =
  | 'high_school'
  | 'undergraduate'
  | 'graduate'
  | 'professional_other'
  | 'unspecified';

export const EDUCATION_LEVEL_LABELS: Record<EducationLevel, string> = {
  high_school: 'High school',
  undergraduate: 'Undergraduate',
  graduate: 'Graduate',
  professional_other: 'Professional / other',
  unspecified: 'Not specified',
};

export interface Course {
  id: number;
  title: string;
  subject_area: string | null;
  education_level: EducationLevel;
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
  subject_area?: string;
  education_level?: EducationLevel;
  description?: string;
  semester?: string;
  exam_date?: string;
  syllabus?: string;
  topics?: string;
}

export interface CourseUpdate {
  title?: string;
  subject_area?: string;
  education_level?: EducationLevel;
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
  profile_knowledge_used?: boolean;
  profile_knowledge_items_used?: number;
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

export interface AiModelInfo {
  id: string;
  provider: string;
  model: string;
  display_name: string;
  is_default: boolean;
}

export interface StudyGuideRequest {
  summary_format: SummaryFormat;
  topic_focus: string;
  summary_length?: SummaryLength;
  detail_level?: DetailLevel;
  summary_mode?: SummaryMode;
  include_profile_context?: boolean;
  model?: string;
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
  include_profile_context?: boolean;
  question_count?: number;
  question_types?: QuizQuestionType[];
  difficulty?: QuizDifficulty;
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
  chunks_ranked?: number;
  chunks_retrieved?: number;
  chunks_used: number;
  chunks_available: number;
  lowest_similarity?: number | null;
  highest_similarity?: number | null;
  truncated: boolean;
  profile_knowledge_used?: boolean;
  profile_knowledge_items_used?: number;
  profile_knowledge_characters_used?: number;
  profile_knowledge_truncated?: boolean;
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

export type QuizQuestionType =
  | 'multiple_choice'
  | 'true_false'
  | 'short_answer'
  | 'open_ended';

export type QuizDifficulty = 'easy' | 'medium' | 'hard';

/** The question types answered by picking an option rather than writing text. */
export const OPTION_BASED_QUESTION_TYPES: readonly QuizQuestionType[] = [
  'multiple_choice',
  'true_false',
];

export const isOptionBased = (questionType: QuizQuestionType): boolean =>
  OPTION_BASED_QUESTION_TYPES.includes(questionType);

export interface QuizRequest {
  question_count: number;
  question_types: QuizQuestionType[];
  difficulty: QuizDifficulty;
  topic_focus: string;
  include_profile_context?: boolean;
  model?: string;
}

/**
 * The stored answer document, discriminated by `type`.
 *
 * `correct_option_index` on the question stays populated for the two
 * option-based types, but this is the authoritative answer.
 */
export type QuizCorrectAnswer =
  | { type: 'multiple_choice'; option_index: number }
  | { type: 'true_false'; value: boolean }
  | { type: 'short_answer'; text: string; accepted_answers: string[] }
  | { type: 'open_ended'; reference_answer: string };

export interface QuizQuestionView {
  question_id: number;
  question_number: number;
  question_type: QuizQuestionType;
  difficulty: QuizDifficulty | null;
  topic: string;
  question: string;
  options: string[] | null;
  correct_option_index: number | null;
  correct_answer: QuizCorrectAnswer | null;
  explanation: string;
}

export interface QuizView {
  quiz_id: number;
  course_id: number;
  title: string;
  created_at: string;
  user_id: number | null;
  model_used: string | null;
  generation_settings: GenerationSettings | null;
  generation_context: GenerationContext | null;
  questions: QuizQuestionView[];
}

export interface QuizSummary {
  quiz_id: number;
  course_id: number;
  title: string;
  question_count: number;
  created_at: string;
  user_id: number | null;
  model_used: string | null;
  generation_settings: GenerationSettings | null;
  generation_context: GenerationContext | null;
}

export interface QuizGenerationResult extends RetrievedContext {
  quiz: QuizView;
  generated_output_id: number;
}

export interface QuizAnswerSubmission {
  question_id: number;
  selected_option_index?: number | null;
  text_response?: string | null;
  time_spent_seconds?: number | null;
}

export interface QuizAttemptRequest {
  answers: QuizAnswerSubmission[];
  time_spent_seconds?: number | null;
}

/**
 * `is_correct` and `score` are both null when the answer could not be graded,
 * which today means an open-ended answer the provider failed to score. An
 * ungraded answer is excluded from the attempt score rather than counted wrong.
 */
export interface QuizAnswerResult {
  question_id: number;
  question_type: QuizQuestionType;
  selected_option_index: number | null;
  text_response: string | null;
  correct_option_index: number | null;
  correct_answer: QuizCorrectAnswer | null;
  is_correct: boolean | null;
  score: number | null;
  feedback: string | null;
  time_spent_seconds?: number | null;
  topic?: string | null;
}

export interface QuizHistoryItem {
  attempt_id: number;
  quiz_id: number;
  score: number;
  correct_count: number;
  total_questions: number;
  time_spent_seconds?: number | null;
  created_at: string;
}

export interface QuizAttemptResponse {
  attempt_id: number;
  quiz_id: number;
  score: number;
  correct_count: number;
  graded_count: number;
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
  quizzes_completed?: number;
  attempts_count: number;
  average_score: number | null;
  correct_count?: number;
  incorrect_count?: number;
  total_questions_answered?: number;
  completion?: number;
  weak_topics?: string[];
  topic_mastery: TopicMastery[];
  quiz_history?: QuizHistoryItem[];
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

export interface FlashcardRequest {
  include_profile_context?: boolean;
  model?: string;
}

export type ConversationType = 'course_qa' | 'ai_tutor';

export type ConversationRole = 'user' | 'assistant';

export interface ConversationMessage {
  id: number;
  role: ConversationRole;
  content: string;
  created_at: string;
}

export interface ConversationSummary {
  id: number;
  course_id: number;
  user_id: number;
  conversation_type: ConversationType;
  preview: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: ConversationMessage[];
}

export interface CourseQARequest {
  question: string;
  conversation_id?: number;
  model?: string;
}

export interface CourseQAGenerationResult extends RetrievedContext {
  answer: string;
  conversation_id: number;
}

export interface PromptGenerationRequest {
  description: string;
  model?: string;
}

export interface PromptGenerationResponse {
  generated_prompt: string;
}

export interface AiTutorRequest {
  question: string;
  conversation_id?: number;
  model?: string;
}

export interface AiTutorGenerationResult extends RetrievedContext {
  answer: string;
  conversation_id: number;
}

export interface GeneratedFlashcard {
  card_number: number;
  difficulty: 'Easy' | 'Medium' | 'Hard';
  front: string;
  back: string;
}

export interface FlashcardGenerationResponse {
  deck_title: string;
  card_count: number;
  flashcards: GeneratedFlashcard[];
}

export interface FlashcardGenerationResult extends BoundedContext {
  flashcards: FlashcardGenerationResponse;
  generated_output_id?: number | null;
}

export interface CourseSettings {
  study_mode: string;
  difficulty: string;
  question_count: number;
  summary_length: string;
  detail_level: string;
  notifications: boolean;
  progress_reminders: boolean;
}

export interface CourseSettingsUpdate {
  study_mode?: string;
  difficulty?: string;
  question_count?: number;
  summary_length?: string;
  detail_level?: string;
  notifications?: boolean;
  progress_reminders?: boolean;
}
