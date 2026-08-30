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
  /** Whether the address was proven reachable. False accounts hold no credits
   * where the deployment verifies; see docs/authentication.md. */
  is_email_verified: boolean;
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

export interface AiCostTotals {
  successful_generations: number;
  prompt_tokens: number;
  completion_tokens: number;
  estimated_cost_usd: number;
  unpriced_generations: number;
}

export interface AiCostDailyRow extends AiCostTotals {
  date: string;
  provider: string;
  model: string;
  pricing_version: string | null;
}

export interface AiCostReport {
  timezone: 'UTC';
  start_date: string;
  end_date: string;
  totals: AiCostTotals;
  daily: AiCostDailyRow[];
}

/** The credit-charging features, keyed as the backend names them. */
export type CreditSource =
  | 'study_guide'
  | 'quiz'
  | 'quiz_open_ended'
  | 'flashcard'
  | 'ai_tutor'
  | 'course_qa'
  | 'prompt_generator'
  | 'exam_topic_analysis'
  | 'exam_topic_analysis_rescan'
  | 'exam_topic_unlock'
  | 'exam_mock_exam'
  | 'exam_review_sheet';

export interface CreditStatus {
  /** null means this account is not metered, so no credit UI applies. */
  credits: number | null;
  metering_enabled: boolean;
  /** Together these tell a zero balance that was spent apart from one that was
   * never granted, which is the difference between no next action and one. */
  email_verification_required: boolean;
  is_email_verified: boolean;
  monthly_grant: number | null;
  balance_cap: number | null;
  next_grant_at: string | null;
  generation_costs: Partial<Record<LooseUnion<CreditSource>, number>>;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
}

/**
 * What registration answers with. The account exists either way; where the
 * deployment verifies, it holds no introductory credits until the emailed link
 * is redeemed, so the screen after this one is a prompt rather than a workspace.
 */
export interface RegistrationResult {
  message: string;
  user_email: string;
  role: string;
  email_verification_required: boolean;
  is_email_verified: boolean;
}

/**
 * The outcome of redeeming or re-requesting a verification link.
 * `credits_granted` is null when this redemption granted nothing — the account
 * was already verified, is unmetered, or had its opening balance before.
 */
export interface EmailVerificationResult {
  message: string;
  is_email_verified: boolean;
  credits_granted: number | null;
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
  owner_name?: string | null;
  owner_email?: string | null;
  created_at: string;
  updated_at: string;
  semester: string | null;
  exam_date: string | null;
  syllabus: string | null;
  topics: string[];
  is_archived?: boolean;
}

export interface CourseCreate {
  title: string;
  subject_area?: string;
  education_level?: EducationLevel;
  description?: string;
  semester?: string;
  exam_date?: string | null;
  syllabus?: string;
  topics?: string[];
  is_archived?: boolean;
}

export interface CourseUpdate {
  title?: string;
  subject_area?: string;
  education_level?: EducationLevel;
  description?: string;
  semester?: string;
  exam_date?: string | null;
  syllabus?: string;
  topics?: string[];
  is_archived?: boolean;
}

export type DocumentStatus =
  | 'uploaded'
  | 'processing'
  | 'ready'
  | 'failed'
  | 'deleting';

export type ProcessingJobStatus = 'queued' | 'running' | 'succeeded' | 'failed';

export type GenerationJobStatus = 'queued' | 'running' | 'succeeded' | 'failed';
export type GenerationJobType = 'generate_study_guide' | 'generate_quiz' | 'generate_flashcard';

export interface GenerationJobAccepted {
  job_id: number;
  status: GenerationJobStatus;
}

export interface GenerationJob {
  id: number;
  job_type: GenerationJobType;
  status: GenerationJobStatus;
  attempt_count: number;
  max_attempts: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  error_message: string | null;
  generated_output_id: number | null;
  quiz_id: number | null;
}

export type ProcessingStage =
  | 'validating'
  | 'extracting_text'
  | 'running_ocr'
  | 'understanding_images'
  | 'cleaning_text'
  | 'chunking'
  | 'generating_embeddings';

export type DocumentMaterialKind =
  | 'lecture_notes'
  | 'slides'
  | 'textbook'
  | 'syllabus'
  | 'assignment'
  | 'past_exam'
  | 'article'
  | 'notes'
  | 'other'
  | 'unspecified';

export type DocumentVisualAnalysisStatus =
  | 'not_applicable'
  | 'pending'
  | 'not_configured'
  | 'completed'
  | 'partial'
  | 'failed';

export interface DocumentResponse {
  id: string;
  original_file_name: string;
  file_type: string;
  mime_type: string;
  material_kind: LooseUnion<DocumentMaterialKind>;
  file_size: number;
  course_id: number;
  status: LooseUnion<DocumentStatus>;
  visual_analysis_status?: LooseUnion<DocumentVisualAnalysisStatus>;
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

export interface ProfileDocumentResponse {
  id: string;
  original_file_name: string;
  file_type: string;
  mime_type: string;
  file_size: number;
  user_id: number;
  status: LooseUnion<DocumentStatus>;
  processing_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProfileDocumentUploadResponse {
  document: ProfileDocumentResponse;
  duplicate: boolean;
}

export interface ProfileDocumentStatusResponse {
  document: ProfileDocumentResponse;
  processing_job: ProcessingJobResponse | null;
}

export interface BoundedContext {
  context_truncated: boolean;
  chunks_used: number;
  chunks_available: number;
  profile_knowledge_used?: boolean;
  profile_knowledge_items_used?: number | null;
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

export type SummaryMode = 'general' | 'exam_focused' | 'last_minute';

export interface AiModelInfo {
  id: string;
  provider: string;
  model: string;
  display_name: string;
  is_default: boolean;
  cost_hint?: string;
  capabilities?: string[];
  description?: string;
  is_local?: boolean;
  supports_json?: boolean;
}

export interface StudyGuideRequest {
  summary_format?: SummaryFormat;
  topic_focus?: string;
  summary_length?: SummaryLength;
  detail_level?: DetailLevel;
  summary_mode?: SummaryMode;
  use_profile_knowledge?: boolean;
  include_profile_context?: boolean;
  model?: string;
}

export interface Citation {
  key: string;
  document_id: string;
  document_label: string;
  page_start: number | null;
  page_end: number | null;
  version?: number;
}

export interface CitedText {
  text: string;
  citations: Citation[];
}

// A guide stored before citations existed holds plain strings in these fields,
// and reopening one must still render, so every citable slot accepts both.
export type MaybeCited = string | CitedText;

export interface ImportantTerm {
  term: string;
  definition: string;
  citations?: Citation[];
}

export interface CommonMistake {
  mistake: string;
  correction: string;
  citations?: Citation[];
}

export interface ExamTips {
  lecture_based: MaybeCited[];
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
  summary: MaybeCited;
  key_points: MaybeCited[];
  important_terms: ImportantTerm[];
  common_mistakes: CommonMistake[];
  exam_tips: ExamTips;
  difficulty: Difficulty;
  estimated_study_time: string;
  prerequisites: MaybeCited[];
  learning_objectives: MaybeCited[];
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
  use_profile_knowledge?: boolean;
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
  question_count?: number;
  question_types?: QuizQuestionType[];
  difficulty?: QuizDifficulty;
  topic_focus?: string;
  use_profile_knowledge?: boolean;
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
  citations?: Citation[];
}

/**
 * What a quiz was written for. Read this rather than parsing a title: the
 * purpose decides how the quiz is labelled and where handing it in returns to.
 */
export type QuizPurpose =
  | 'practice'
  | 'exam_topic_practice'
  | 'exam_topic_exam'
  | 'exam_similar_questions'
  | 'exam_mock_exam';

export interface QuizView {
  quiz_id: number;
  course_id: number;
  title: string;
  created_at: string;
  user_id: number | null;
  model_used: string | null;
  generation_settings: GenerationSettings | null;
  generation_context: GenerationContext | null;
  quiz_purpose: LooseUnion<QuizPurpose> | null;
  exam_plan_output_id: number | null;
  exam_topic_key: string | null;
  timed: boolean;
  time_limit_seconds: number | null;
  answers_hidden: boolean;
  questions: QuizQuestionView[];
}

export interface QuizSummary {
  quiz_id: number;
  course_id: number;
  title: string;
  question_count: number;
  attempts_count?: number;
  best_score?: number | null;
  last_score?: number | null;
  created_at: string;
  user_id: number | null;
  model_used: string | null;
  generation_settings: GenerationSettings | null;
  generation_context: GenerationContext | null;
  quiz_purpose: LooseUnion<QuizPurpose> | null;
  exam_plan_output_id: number | null;
  exam_topic_key: string | null;
  timed: boolean;
  time_limit_seconds: number | null;
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
  quiz_purpose: LooseUnion<QuizPurpose> | null;
  timed: boolean;
  expired: boolean;
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
  quiz_purpose: LooseUnion<QuizPurpose> | null;
  timed: boolean;
  expired: boolean;
  answers: QuizAnswerResult[];
}

export type QuizSessionStatus = 'active' | 'submitted' | 'expired';

/**
 * One timed sitting as the server reports it.
 *
 * `expires_at` is the server's deadline and the only thing a countdown may be
 * built from; `seconds_remaining` is a convenience derived from the same
 * reading, never a second source of truth. `answers` carries every draft saved
 * so far, which is what lets a reload put the candidate's own work back on the
 * screen instead of showing them a blank paper the server would still grade.
 */
export interface QuizSessionView {
  session_id: number;
  quiz_id: number;
  status: LooseUnion<QuizSessionStatus>;
  started_at: string;
  expires_at: string;
  time_limit_seconds: number;
  seconds_remaining: number;
  elapsed_seconds: number;
  answered_count: number;
  answers: QuizAnswerSubmission[];
  attempt_id: number | null;
}

export interface QuizSessionStartResult {
  session: QuizSessionView;
  quiz: QuizView;
}

export type MasteryStatus = 'Mastered' | 'In Progress' | 'Needs Review';

export interface TopicMastery {
  topic: string;
  questions_answered: number;
  questions_correct: number;
  mastery_percentage: number;
  status: LooseUnion<MasteryStatus>;
}

export type CourseStatus =
  | 'no_documents'
  | 'processing'
  | 'ready'
  | 'practiced'
  | 'mastered';

export const COURSE_STATUS_LABELS: Record<CourseStatus, string> = {
  no_documents: 'No sources ready',
  processing: 'Processing',
  ready: 'Ready to study',
  practiced: 'Practiced',
  mastered: 'Mastered',
};

export interface CourseProgressResponse {
  status: LooseUnion<CourseStatus>;
  quizzes_completed?: number;
  attempts_count: number;
  average_score: number | null;
  total_time_spent_seconds?: number | null;
  correct_count?: number;
  incorrect_count?: number;
  total_questions_answered?: number;
  completion?: number;
  weak_topics?: string[];
  topic_mastery: TopicMastery[];
  quiz_history?: QuizHistoryItem[];
}

export interface CourseProgressSummary {
  course_id: number;
  status: LooseUnion<CourseStatus>;
  attempts_count: number;
  average_score: number | null;
  completion: number | null;
  total_time_spent_seconds?: number | null;
  last_activity: string | null;
}

export type ActivityKind = 'generation' | 'attempt';

export interface ActivityItem {
  kind: ActivityKind;
  action_type: string;
  course_id: number;
  course_title: string;
  occurred_at: string;
  output_id: number | null;
  quiz_id: number | null;
  attempt_id: number | null;
  topic: string | null;
  score: number | null;
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
  topic_focus?: string;
  use_profile_knowledge?: boolean;
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
  citations?: Citation[];
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
  use_profile_knowledge?: boolean;
  include_profile_context?: boolean;
  model?: string;
}

export interface CourseQAGenerationResult extends RetrievedContext {
  answer: string;
  citations?: Citation[];
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
  use_profile_knowledge?: boolean;
  include_profile_context?: boolean;
  model?: string;
}

export interface AiTutorGenerationResult extends RetrievedContext {
  answer: string;
  citations?: Citation[];
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

export interface FlashcardGenerationResult extends RetrievedContext {
  flashcards: FlashcardGenerationResponse;
  generated_output_id?: number | null;
}

export interface CourseSettings {
  study_mode: string;
  difficulty: string;
  question_count: number;
  summary_length: string;
  detail_level: string;
}

export interface CourseSettingsUpdate {
  study_mode?: string;
  difficulty?: string;
  question_count?: number;
  summary_length?: string;
  detail_level?: string;
}

export type RoadmapDayKind = 'study' | 'review' | 'final_review' | 'last_minute';
export type RoadmapHorizon = 'zero_day' | 'one_day' | 'standard' | 'long';
export type TopicSource = 'syllabus' | 'quiz' | 'exam_plan';
export type TopicMaterialStatus =
  | 'resolved'
  | 'no_match'
  | 'not_indexed'
  | 'no_material'
  | 'not_requested';
export type DeferralReason = 'horizon_too_short';

export interface RoadmapMaterial {
  document_id: string;
  document_label: string;
  page_start?: number | null;
  page_end?: number | null;
}

export interface RankedTopicView {
  topic: string;
  /** The canonical key when the roadmap was built from an exam plan. */
  topic_key?: string | null;
  source: TopicSource;
  syllabus_position?: number | null;
  importance: number;
  mastery_percentage?: number | null;
  /** Null when the ranking never measured one, which is not the same as zero. */
  questions_answered: number | null;
  priority: number;
}

export interface RoadmapTopic {
  topic: string;
  topic_key?: string | null;
  goal: string;
  pass_number: number;
  source: TopicSource;
  syllabus_position?: number | null;
  importance: number;
  mastery_percentage?: number | null;
  questions_answered: number | null;
  priority: number;
  material_status: TopicMaterialStatus;
  materials: RoadmapMaterial[];
  citations: Citation[];
}

export interface RoadmapDay {
  day_index: number;
  date: string;
  kind: RoadmapDayKind;
  is_exam_day: boolean;
  focus: string;
  topics: RoadmapTopic[];
}

export interface DeferredTopic {
  topic: string;
  priority: number;
  reason: DeferralReason;
}

export interface ExamRoadmap {
  version: 1;
  output_type: 'exam_roadmap';
  course_id: number;
  exam_date: string;
  generated_on: string;
  starts_on: string;
  days_until_exam: number;
  scheduled_days: number;
  lead_in_days: number;
  horizon: RoadmapHorizon;
  materials_available: boolean;
  attempts_considered: number;
  roadmap_version: number;
  adapted_from_output_id?: number | null;
  /** The exam plan this schedule follows, or null for a course-wide roadmap. */
  plan_output_id?: number | null;
  ranked_topics: RankedTopicView[];
  days: RoadmapDay[];
  deferred_topics: DeferredTopic[];
  notes: string[];
}

export interface ExamRoadmapRequest {
  /** Schedule this plan's ranked topics instead of the course's declared ones. */
  plan_output_id?: number | null;
  max_topics_per_day?: number;
  include_materials?: boolean;
}

export interface ExamRoadmapResult {
  roadmap: ExamRoadmap;
  generated_output_id: number;
}

// ---------------------------------------------------------------- Exam Mode
//
// Mirrors schemas/exam_mode.py. Three families meet here: an analysis discovers
// topics, a plan ranks the ones the student selected, and every artifact hangs
// off a plan. Ranking is the backend's -- nothing here recomputes a score, an
// order, or an explanation.

export interface ExamSourceDocument {
  id: string;
  label: string;
  material_kind: string;
  status: string;
  is_past_exam: boolean;
  is_syllabus: boolean;
}

export interface ExamSourceInventory {
  syllabus_present: boolean;
  syllabus_characters: number;
  course_topics: string[];
  documents: ExamSourceDocument[];
  ready_document_count: number;
  past_exam_document_count: number;
  chunks_available: number;
}

export interface ExamEntitlementView {
  unlocked_topic_keys: string[];
}

export interface ExamAnalysisRequest {
  document_ids?: string[] | null;
  topic_focus?: string;
  model?: string | null;
}

export interface ExamTopicCandidateView {
  topic_key: string;
  display_label: string;
  aliases: string[];
  in_syllabus: boolean;
  in_course_topics: boolean;
  in_past_exams: boolean;
  in_material: boolean;
  discovery_confidence: number;
  syllabus_weight_percent?: number | null;
  syllabus_mention_count: number;
  past_exam_question_count: number;
  material_chunk_count: number;
  citations: Citation[];
}

/**
 * What a previous plan's choices mean against a fresh analysis. Read-only: the
 * backend never re-applies a selection, so a student is told what carried over
 * and what did not rather than having it decided for them.
 */
export interface ExamSelectionCarryOver {
  previous_plan_output_id?: number | null;
  preselected_topic_keys: string[];
  high_priority_topic_keys: string[];
  new_topic_keys: string[];
  unsupported_topic_keys: string[];
}

export interface ExamAnalysisView {
  generated_output_id: number;
  created_at: string;
  model_used: string | null;
  candidate_count: number;
  past_exam_question_count: number;
  documents_analysed: string[];
  manual_review_recommended: boolean;
  topics: ExamTopicCandidateView[];
  selection_carry_over: ExamSelectionCarryOver;
  coverage?: Record<string, unknown> | null;
  confidence_notes: string;
}

export interface ExamAnalysisResult extends RetrievedContext {
  analysis: ExamAnalysisView;
}

export interface ExamQuestionView {
  position: number;
  document_id: string;
  page_start?: number | null;
  page_end?: number | null;
  question_label?: string | null;
  question_number?: number | null;
  question_text: string;
  subparts: Record<string, unknown>[];
  question_type: string;
  difficulty?: string | null;
  marks?: number | null;
  answer_guidance?: string | null;
  marking_points: string[];
  visual_refs: Record<string, unknown>[];
  topic_key?: string | null;
  topic_mappings: Record<string, unknown>[];
  citations: Citation[];
}

export interface ExamQuestionPage {
  analysis_output_id: number;
  document_ids: string[];
  total: number;
  limit: number;
  offset: number;
  questions: ExamQuestionView[];
}

export type ExamSelectionMode = 'manual' | 'all_discovered';

export interface ExamPlanRequest {
  analysis_output_id?: number | null;
  selected_topic_keys: string[];
  high_priority_topic_keys: string[];
  selection_mode?: ExamSelectionMode;
}

/**
 * One ranked topic. `explanation` is assembled by the backend from constants
 * and is the sentence to show; `signals` and `reason_codes` are the audit trail
 * behind it. A null `mastery_percentage` means unattempted, never zero, and
 * `has_any_evidence: false` means no signal, never negative evidence.
 */
export interface ExamPlanTopicView {
  topic_key: string;
  display_label: string;
  rank: number;
  is_high_priority: boolean;
  priority_score: number;
  priority_band: string;
  has_any_evidence: boolean;
  is_unattempted: boolean;
  mastery_percentage: number | null;
  signals: Record<string, unknown>;
  reason_codes: string[];
  explanation: string;
}

/**
 * `is_stale` and `requires_rescan` are different facts with different remedies:
 * a moved exam date only reorders, while a changed source has to be read again.
 */
export interface ExamPlanStaleness {
  is_stale: boolean;
  requires_rescan: boolean;
  stale_reasons: string[];
}

export interface ExamPlanView {
  generated_output_id: number;
  analysis_output_id: number;
  plan_version: number;
  supersedes_output_id?: number | null;
  created_at: string;
  exam_date?: string | null;
  days_until_exam?: number | null;
  selection_mode: string;
  manual_review_recommended: boolean;
  ranking_engine: string;
  ranking_policy_version: number;
  configured_weights: Record<string, number>;
  effective_weights: Record<string, number>;
  signals_available: Record<string, boolean>;
  signal_bases: Record<string, string>;
  unmapped_mastery_labels: number;
  warnings: string[];
  topics: ExamPlanTopicView[];
  staleness: ExamPlanStaleness;
}

export interface ExamPlanSummary {
  generated_output_id: number;
  analysis_output_id: number;
  plan_version: number;
  supersedes_output_id?: number | null;
  created_at: string;
  exam_date?: string | null;
  topic_count: number;
  selection_mode: string;
  is_current: boolean;
}

export interface ExamPlanList {
  plans: ExamPlanSummary[];
  current_plan_output_id?: number | null;
}

export interface ExamTopicArtifactRequest {
  plan_output_id?: number | null;
  model?: string | null;
}

export interface ExamTopicQuizRequest extends ExamTopicArtifactRequest {
  question_count?: number | null;
}

export interface ExamPlanArtifactRequest {
  plan_output_id?: number | null;
  model?: string | null;
}

export interface ExamTopicSection {
  heading: string;
  body: MaybeCited;
  key_points: MaybeCited[];
}

export interface ExamTopicTerm {
  term: string;
  definition: string;
  citations: Citation[];
}

export interface ExamTopicPitfall {
  mistake: string;
  correction: string;
  citations: Citation[];
}

export interface ExamTopicGuideDocument {
  version: 1;
  output_type: 'exam_topic_guide';
  topic_key: string;
  display_label: string;
  plan_output_id: number;
  rank: number;
  priority_band: string;
  title: string;
  overview: MaybeCited;
  sections: ExamTopicSection[];
  key_terms: ExamTopicTerm[];
  common_pitfalls: ExamTopicPitfall[];
  what_to_be_able_to_do: MaybeCited[];
  coverage?: Coverage | null;
  confidence_notes: string;
}

export interface ExamTopicSummaryDocument {
  version: 1;
  output_type: 'exam_topic_summary';
  topic_key: string;
  display_label: string;
  plan_output_id: number;
  rank: number;
  priority_band: string;
  title: string;
  summary: MaybeCited;
  key_points: MaybeCited[];
  coverage?: Coverage | null;
  confidence_notes: string;
}

interface ExamArtifactResult extends RetrievedContext {
  generated_output_id: number;
  created_at: string;
  model_used: string | null;
  credits_charged: number;
}

export interface ExamTopicGuideResult extends ExamArtifactResult {
  guide: ExamTopicGuideDocument;
}

export interface ExamTopicSummaryResult extends ExamArtifactResult {
  summary: ExamTopicSummaryDocument;
}

export interface ExamTopicQuizResult extends ExamArtifactResult {
  quiz: QuizView;
  answers_hidden: boolean;
}

export type SimilarQuestionDifficultyPolicy = 'match_source' | 'easy' | 'medium' | 'hard';

export interface SimilarQuestionRequest extends ExamTopicArtifactRequest {
  source_question_ids?: number[] | null;
  question_count?: number;
  difficulty_policy?: SimilarQuestionDifficultyPolicy;
  requested_question_types?: QuizQuestionType[] | null;
  request_id?: string | null;
}

export interface ExamSimilarQuestionsResult extends ExamArtifactResult {
  quiz: QuizView;
  answers_hidden: boolean;
  source_question_ids: number[];
}

export interface MockExamQuestionMixEntry {
  question_type: QuizQuestionType;
  count: number;
}

export interface ExamMockExamRequest extends ExamPlanArtifactRequest {
  question_count?: number | null;
  duration_minutes?: number;
  question_mix?: MockExamQuestionMixEntry[] | null;
  topic_keys?: string[] | null;
  request_id?: string | null;
}

export interface ExamMockExamResult extends ExamArtifactResult {
  quiz: QuizView;
  answers_hidden: boolean;
  duration_minutes: number;
  time_limit_seconds: number;
}

export interface ExamReviewTopic {
  topic_key: string;
  topic_label: string;
  must_remember: MaybeCited[];
  traps: MaybeCited[];
}

export interface ExamReviewSheetDocument {
  version: 1;
  output_type: 'exam_review_sheet';
  plan_output_id: number;
  exam_date?: string | null;
  days_until_exam?: number | null;
  title: string;
  topics: ExamReviewTopic[];
  final_checks: MaybeCited[];
  confidence_notes: string;
}

export interface ExamReviewSheetResult extends ExamArtifactResult {
  review_sheet: ExamReviewSheetDocument;
}

export type ConceptStatus =
  | 'unsupported'
  | 'absent'
  | 'partially_correct'
  | 'contradicted';

export interface Misconception {
  concept: string;
  status: LooseUnion<ConceptStatus>;
  detail: string;
}

export interface ReverseQuizRequest {
  topic: string;
  explanation: string;
  question?: string | null;
}

export interface ReverseQuizResponse {
  id: number;
  course_id: number;
  topic: string;
  explanation: string;
  feedback: string;
  misconceptions: Misconception[];
  question?: string | null;
}

export interface ReverseQuizQuestion {
  topic: string;
  question: string;
}

export interface ReverseQuizQuestionsResponse {
  course_id: number;
  questions: ReverseQuizQuestion[];
}

export type AdPlacement = 'sidebar' | 'footer' | 'dashboard' | 'landing';
export type AdStatus = 'rendered' | 'blocked' | 'no_fill' | 'error';

export interface AdConfigResponse {
  enabled: boolean;
  provider: string | null;
  publisher_id: string | null;
}

export interface AdTelemetryRequest {
  placement: AdPlacement;
  provider: string;
  status: AdStatus;
}

export interface AdTelemetryResponse {
  recorded: boolean;
}

export interface UserApiKeys {
  openai_api_key: string | null;
  gemini_api_key: string | null;
  anthropic_api_key: string | null;
  has_openai_key: boolean;
  has_gemini_key: boolean;
  has_anthropic_key: boolean;
}

export interface UserApiKeysUpdateRequest {
  openai_api_key?: string | null;
  gemini_api_key?: string | null;
  anthropic_api_key?: string | null;
}
