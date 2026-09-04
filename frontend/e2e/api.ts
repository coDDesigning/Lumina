import type { Page, Route } from '@playwright/test'

/**
 * The browser suite answers its own API calls. Nothing outside `frontend/`
 * has to be running, installed, or even present, so the suite is hermetic and
 * CI needs no second process.
 *
 * Shapes follow the real contracts in `src/api/types.ts`. Most reads are
 * wrapped in the `BaseResponse` envelope the client unwraps; the two auth
 * routes are not, matching the backend.
 */

const envelope = (data: unknown) => ({ success: true, message: 'ok', data })

let quizJobQueued = false

export const USER = {
  id: 1,
  name: 'Bora Kafadar',
  email: 'bora@example.com',
  role: 'admin',
  is_banned: false,
  credits: 14,
  preferred_model: 'gemini-3.6-flash',
  education_level: 'undergraduate',
}

export const COURSE = {
  id: 1,
  owner_id: 1,
  title: 'Fundamental Structures of Computer Science',
  subject_area: 'Computer Science',
  education_level: 'undergraduate',
  semester: 'Fall 2026',
  exam_date: '2026-09-04',
  topics: ['Graph Traversals', 'Shortest Paths', 'Minimum Spanning Trees'],
  syllabus: '',
  description: 'Graphs, trees, and the algorithms over them.',
  created_at: '2026-08-01T09:00:00Z',
  updated_at: '2026-08-20T09:00:00Z',
}

const COURSES = [
  COURSE,
  { ...COURSE, id: 2, title: 'Linear Algebra', subject_area: 'Mathematics', topics: ['Eigenvalues'] },
]

const DOCUMENTS = [
  {
    id: '11111111-1111-1111-1111-111111111111',
    original_file_name: 'lecture-07-graph-traversals.pdf',
    file_type: 'pdf',
    mime_type: 'application/pdf',
    material_kind: 'slides',
    file_size: 2_517_000,
    course_id: 1,
    status: 'ready',
    created_at: '2026-08-10T09:00:00Z',
    updated_at: '2026-08-10T09:04:00Z',
  },
]

const PROGRESS = {
  status: 'practiced',
  attempts_count: 2,
  quizzes_completed: 2,
  average_score: 0.7,
  total_time_spent_seconds: 4320,
  correct_count: 11,
  incorrect_count: 4,
  total_questions_answered: 15,
  completion: 70,
  weak_topics: ['Graph Traversals'],
  topic_mastery: [
    {
      topic: 'Graph Traversals',
      questions_answered: 4,
      questions_correct: 1,
      mastery_percentage: 25,
      status: 'Needs Review',
    },
    {
      topic: 'Shortest Paths',
      questions_answered: 6,
      questions_correct: 6,
      mastery_percentage: 100,
      status: 'Mastered',
    },
  ],
  quiz_history: [],
}

const PROGRESS_SUMMARIES = COURSES.map((course) => ({
  course_id: course.id,
  status: 'practiced',
  attempts_count: 2,
  average_score: 0.7,
  completion: 70,
  total_time_spent_seconds: 4320,
  last_activity: '2026-08-22T14:00:00Z',
}))

const CREDITS = {
  credits: 14,
  metering_enabled: true,
  monthly_grant: 20,
  balance_cap: 40,
  next_grant_at: '2026-09-01T00:00:00Z',
  generation_costs: {
    study_guide: 1,
    quiz: 1,
    quiz_open_ended: 2,
    flashcard: 1,
    course_qa: 1,
    ai_tutor: 1,
    prompt_generator: 1,
  },
}

const SETTINGS = {
  study_mode: 'exam_focused',
  difficulty: 'medium',
  question_count: 10,
  summary_length: 'medium',
  detail_level: 'standard',
}

const MODELS = [
  {
    id: 'gemini-3.6-flash',
    model: 'gemini-3.6-flash',
    display_name: 'Gemini 3.6 Flash',
    provider: 'gemini',
    is_default: true,
    is_local: false,
    supports_json: true,
    description: 'Fast, high quality, good at JSON.',
    cost_hint: 'Metered (1-2 credits)',
    capabilities: ['study_guide', 'quiz', 'flashcard'],
  },
]

const KNOWLEDGE = [
  {
    id: 1,
    user_id: 1,
    topic: 'University and department',
    detail: 'Computer Engineering. Courses are taught in English.',
    created_at: '2026-08-01T09:00:00Z',
    updated_at: '2026-08-01T09:00:00Z',
  },
]

const ADMIN_USERS = [
  USER,
  { ...USER, id: 2, name: 'Ada Lovelace', email: 'ada@example.com', role: 'student', credits: 6 },
]

const AI_COSTS = {
  timezone: 'UTC',
  start_date: '2026-07-27',
  end_date: '2026-08-26',
  totals: {
    successful_generations: 12,
    prompt_tokens: 48_000,
    completion_tokens: 9_400,
    estimated_cost_usd: 0.34,
    unpriced_generations: 0,
  },
  daily: [],
}

const CITATION = {
  key: 'S1',
  document_id: '11111111-1111-1111-1111-111111111111',
  document_label: 'Lecture 4',
  page_start: 12,
  page_end: 14,
}

export { EXAM_PLAN, EXAM_ROADMAP }

export const GENERATION_JOB = {
  id: 12,
  job_type: 'generate_quiz',
  status: 'succeeded',
  attempt_count: 1,
  max_attempts: 3,
  created_at: '2026-08-22T12:59:00Z',
  started_at: '2026-08-22T12:59:10Z',
  finished_at: '2026-08-22T13:00:00Z',
  error_code: null,
  error_message: null,
  generated_output_id: null,
  quiz_id: 4,
}

export const QUIZ = {
  quiz_id: 4,
  course_id: 1,
  title: 'Graph traversals',
  created_at: '2026-08-22T13:00:00Z',
  user_id: 1,
  model_used: 'gemini-3.6-flash',
  generation_settings: null,
  generation_context: null,
  questions: [
    {
      question_id: 1,
      question_number: 1,
      question_type: 'multiple_choice',
      difficulty: 'medium',
      topic: 'Shortest Paths',
      question: 'Which algorithm finds the fewest-edge path in an unweighted graph?',
      options: ['Dijkstra', 'Breadth-first search', 'Depth-first search', 'Bellman-Ford'],
      correct_option_index: 1,
      correct_answer: { type: 'multiple_choice', option_index: 1 },
      explanation: 'BFS settles vertices in order of edge count.',
      citations: [CITATION],
    },
    {
      question_id: 2,
      question_number: 2,
      question_type: 'true_false',
      difficulty: 'easy',
      topic: 'Shortest Paths',
      question: 'Dijkstra is correct on graphs with negative edge weights.',
      options: ['True', 'False'],
      correct_option_index: 1,
      correct_answer: { type: 'true_false', value: false },
      explanation: 'A settled vertex can still be improved by a later negative edge.',
    },
    {
      question_id: 3,
      question_number: 3,
      question_type: 'open_ended',
      difficulty: 'hard',
      topic: 'Graph Traversals',
      question: 'Explain why DFS finishing times give a reverse topological order.',
      options: null,
      correct_option_index: null,
      correct_answer: {
        type: 'open_ended',
        reference_answer: 'A vertex finishes only after all its descendants.',
      },
      explanation: 'Finishing order reverses the dependency order.',
    },
  ],
}

/** One correct, one wrong, and one the model could not score. */
const ATTEMPT = {
  attempt_id: 9,
  quiz_id: 4,
  score: 0.5,
  correct_count: 1,
  graded_count: 2,
  total_questions: 3,
  time_spent_seconds: 412,
  created_at: '2026-08-22T14:00:00Z',
  answers: [
    {
      question_id: 1,
      question_type: 'multiple_choice',
      selected_option_index: 1,
      text_response: null,
      correct_option_index: 1,
      correct_answer: null,
      is_correct: true,
      score: 1,
      feedback: null,
    },
    {
      question_id: 2,
      question_type: 'true_false',
      selected_option_index: 0,
      text_response: null,
      correct_option_index: 1,
      correct_answer: null,
      is_correct: false,
      score: 0,
      feedback: null,
    },
    {
      question_id: 3,
      question_type: 'open_ended',
      selected_option_index: null,
      text_response: 'Because a vertex finishes after everything it points to.',
      correct_option_index: null,
      correct_answer: null,
      is_correct: null,
      score: null,
      feedback: null,
    },
  ],
}

const CONTEXT = {
  context_truncated: false,
  chunks_used: 4,
  chunks_available: 9,
  retrieval_narrowed: true,
  lowest_similarity: 0.41,
  highest_similarity: 0.88,
}

// --------------------------------------------------------------- Exam Mode

const EXAM_SOURCES = {
  syllabus_present: true,
  syllabus_characters: 820,
  course_topics: ['Graph Traversal Algorithms'],
  documents: [
    {
      id: '0f5f4a1e-0000-4000-8000-000000000001',
      label: 'Lecture 4 — Graphs',
      material_kind: 'lecture',
      status: 'ready',
      is_past_exam: false,
      is_syllabus: false,
    },
    {
      id: '0f5f4a1e-0000-4000-8000-000000000002',
      label: '2025 Final Paper',
      material_kind: 'past_exam',
      status: 'ready',
      is_past_exam: true,
      is_syllabus: false,
    },
    {
      id: '0f5f4a1e-0000-4000-8000-000000000003',
      label: 'Week 9 slides',
      material_kind: 'lecture',
      status: 'processing',
      is_past_exam: false,
      is_syllabus: false,
    },
  ],
  ready_document_count: 2,
  past_exam_document_count: 1,
  chunks_available: 42,
}

const EXAM_TOPIC_KEYS = [
  'graph-traversal-algorithms',
  'dynamic-programming',
  'hashing-and-collision-resolution',
  'asymptotic-analysis',
]

const EXAM_LABELS: Record<string, string> = {
  'graph-traversal-algorithms': 'Graph Traversal Algorithms',
  'dynamic-programming': 'Dynamic Programming',
  'hashing-and-collision-resolution': 'Hashing and Collision Resolution',
  'asymptotic-analysis': 'Asymptotic Analysis',
}

const EXAM_ANALYSIS = {
  generated_output_id: 501,
  created_at: '2026-05-01T09:00:00Z',
  model_used: 'ollama:qwen3:8b',
  candidate_count: EXAM_TOPIC_KEYS.length,
  past_exam_question_count: 2,
  documents_analysed: [
    '0f5f4a1e-0000-4000-8000-000000000001',
    '0f5f4a1e-0000-4000-8000-000000000002',
  ],
  manual_review_recommended: true,
  topics: EXAM_TOPIC_KEYS.map((key, index) => ({
    topic_key: key,
    display_label: EXAM_LABELS[key],
    aliases: [],
    in_syllabus: index === 0,
    in_course_topics: index === 0,
    in_past_exams: index < 2,
    in_material: true,
    discovery_confidence: 0.8,
    syllabus_weight_percent: index === 0 ? 30 : null,
    syllabus_mention_count: index === 0 ? 3 : 0,
    past_exam_question_count: index < 2 ? 2 : 0,
    material_chunk_count: 6 - index,
    citations: [CITATION],
  })),
  selection_carry_over: {
    previous_plan_output_id: null,
    preselected_topic_keys: [],
    high_priority_topic_keys: [],
    new_topic_keys: [],
    unsupported_topic_keys: [],
  },
  coverage: null,
  confidence_notes: '',
}

const EXAM_PLAN = {
  generated_output_id: 601,
  analysis_output_id: 501,
  plan_version: 3,
  supersedes_output_id: 600,
  created_at: '2026-05-01T10:00:00Z',
  exam_date: '2026-09-16',
  days_until_exam: 138,
  selection_mode: 'all_discovered',
  manual_review_recommended: true,
  ranking_engine: 'deterministic',
  ranking_policy_version: 1,
  configured_weights: { syllabus: 30, past_exams: 25, mastery: 25, material: 20 },
  effective_weights: { mastery: 56, material: 44 },
  signals_available: { syllabus: false, past_exams: false, mastery: true, material: true },
  signal_bases: { mastery: 'attempts', material: 'chunks' },
  unmapped_mastery_labels: 2,
  warnings: ['no_syllabus_evidence', 'no_past_exam_evidence', 'unmapped_mastery_labels'],
  topics: EXAM_TOPIC_KEYS.map((key, index) => ({
    topic_key: key,
    display_label: EXAM_LABELS[key],
    rank: index + 1,
    is_high_priority: index === 0,
    priority_score: 88 - index * 14,
    priority_band: ['critical', 'high', 'medium', 'low'][index],
    has_any_evidence: true,
    is_unattempted: index > 1,
    mastery_percentage: index === 0 ? 0 : index === 1 ? 45 : null,
    signals: { mastery: { normalized_value: 1 }, syllabus: null },
    reason_codes: ['weak_mastery'],
    explanation:
      'You are scoring 0% on it. Your course material covers it in depth. No syllabus evidence was available, so its weighting was redistributed.',
  })),
  staleness: { is_stale: false, requires_rescan: false, stale_reasons: [] },
}

const EXAM_PLAN_LIST = {
  current_plan_output_id: 601,
  plans: [
    {
      generated_output_id: 601,
      analysis_output_id: 501,
      plan_version: 3,
      supersedes_output_id: 600,
      created_at: '2026-05-01T10:00:00Z',
      exam_date: '2026-09-16',
      topic_count: 4,
      selection_mode: 'all_discovered',
      is_current: true,
    },
    {
      generated_output_id: 600,
      analysis_output_id: 501,
      plan_version: 2,
      supersedes_output_id: null,
      created_at: '2026-04-28T10:00:00Z',
      exam_date: '2026-09-16',
      topic_count: 3,
      selection_mode: 'manual',
      is_current: false,
    },
  ],
}

const EXAM_QUESTIONS = {
  analysis_output_id: 501,
  document_ids: ['0f5f4a1e-0000-4000-8000-000000000002'],
  total: 2,
  limit: 50,
  offset: 0,
  questions: [
    {
      position: 1,
      document_id: '0f5f4a1e-0000-4000-8000-000000000002',
      page_start: 2,
      page_end: 2,
      question_label: 'Q3(b)',
      question_number: 3,
      question_text: 'Trace breadth-first search over the graph in Figure 2.',
      subparts: [],
      question_type: 'structured',
      difficulty: 'medium',
      marks: 8,
      answer_guidance: null,
      marking_points: [],
      visual_refs: [],
      topic_key: 'graph-traversal-algorithms',
      topic_mappings: [],
      citations: [CITATION],
    },
  ],
}

const EXAM_ROADMAP = {
  version: 1,
  output_type: 'exam_roadmap',
  course_id: 1,
  exam_date: '2026-09-16',
  generated_on: '2026-05-01',
  starts_on: '2026-05-01',
  days_until_exam: 138,
  scheduled_days: 2,
  lead_in_days: 0,
  horizon: 'standard',
  materials_available: true,
  attempts_considered: 3,
  roadmap_version: 1,
  adapted_from_output_id: null,
  plan_output_id: 601,
  ranked_topics: [],
  days: [
    {
      day_index: 1,
      date: '2026-05-01',
      kind: 'study',
      is_exam_day: false,
      focus: 'Graph Traversal Algorithms',
      topics: [
        {
          topic: 'Graph Traversal Algorithms',
          topic_key: 'graph-traversal-algorithms',
          goal: 'Work through breadth-first and depth-first search until you can trace both.',
          pass_number: 1,
          source: 'exam_plan',
          syllabus_position: 1,
          importance: 0.88,
          mastery_percentage: 0,
          questions_answered: null,
          priority: 0.88,
          material_status: 'resolved',
          materials: [],
          citations: [CITATION],
        },
      ],
    },
    {
      day_index: 2,
      date: '2026-05-02',
      kind: 'final_review',
      is_exam_day: false,
      focus: 'Dynamic Programming',
      topics: [
        {
          topic: 'Dynamic Programming',
          topic_key: 'dynamic-programming',
          goal: 'Revisit memoisation until the recurrence comes without prompting.',
          pass_number: 1,
          source: 'exam_plan',
          syllabus_position: 2,
          importance: 0.74,
          mastery_percentage: 45,
          questions_answered: null,
          priority: 0.74,
          material_status: 'no_match',
          materials: [],
          citations: [],
        },
      ],
    },
  ],
  deferred_topics: [],
  notes: [],
}

const EXAM_REVIEW_SHEET = {
  version: 1,
  output_type: 'exam_review_sheet',
  plan_output_id: 601,
  exam_date: '2026-09-16',
  days_until_exam: 138,
  title: 'Last-minute review',
  topics: [
    {
      topic_key: 'graph-traversal-algorithms',
      topic_label: 'Graph Traversal Algorithms',
      must_remember: [{ text: 'BFS settles vertices in order of edge count.', citations: [CITATION] }],
      traps: [{ text: 'Marking a vertex visited on pop rather than on push.', citations: [] }],
    },
  ],
  final_checks: [{ text: 'Check every recurrence has a base case.', citations: [] }],
  confidence_notes: '',
}

const TIMED_QUIZ = {
  ...QUIZ,
  quiz_id: 9,
  title: 'Mock exam',
  quiz_purpose: 'exam_mock_exam',
  exam_plan_output_id: 601,
  exam_topic_key: null,
  timed: true,
  time_limit_seconds: 3600,
  answers_hidden: true,
}

/** A sitting with work already in it, so a reload has something to restore. */
const QUIZ_SESSION = {
  session_id: 55,
  quiz_id: 9,
  status: 'active',
  started_at: new Date(Date.now() - 60_000).toISOString(),
  expires_at: new Date(Date.now() + 3_540_000).toISOString(),
  time_limit_seconds: 3600,
  seconds_remaining: 3540,
  elapsed_seconds: 60,
  answered_count: 1,
  answers: [{ question_id: 1, selected_option_index: 1, text_response: null }],
  attempt_id: null,
}

type Answer = [RegExp, (match: RegExpMatchArray) => unknown]

const ROUTES: Answer[] = [
  [/^\/api\/auth\/me$/, () => USER],
  [/^\/api\/auth\/login$/, () => ({ access_token: 'stub', token_type: 'bearer', user: USER })],
  [/^\/api\/auth\/register$/, () => envelope(USER)],
  [/^\/api\/users\/me\/password$/, () => envelope(null)],
  [/^\/api\/users\/me\/credits$/, () => envelope(CREDITS)],
  [/^\/api\/users\/me\/credit-transactions/, () => envelope([])],
  [/^\/api\/models/, () => envelope(MODELS)],
  [/^\/api\/profile-knowledge/, () => envelope(KNOWLEDGE)],
  [/^\/api\/activity/, () => envelope([])],
  [/^\/api\/progress\/?$/, () => envelope(PROGRESS_SUMMARIES)],
  [/^\/api\/admin\/users/, () => envelope(ADMIN_USERS)],
  [/^\/api\/admin\/ai-costs/, () => envelope(AI_COSTS)],
  [
    /^\/api\/courses\/(\d+)\/exam-mode\/topics\/([^/]+)\/guide$/,
    () => ({ success: false, message: 'not generated', data: null }),
  ],
  [
    /^\/api\/courses\/(\d+)\/exam-mode\/topics\/([^/]+)\/similar-questions$/,
    () => envelope({ ...EXAM_QUESTIONS }),
  ],
  [/^\/api\/courses\/(\d+)\/exam-mode\/sources$/, () => envelope(EXAM_SOURCES)],
  [/^\/api\/courses\/(\d+)\/exam-mode\/entitlements$/, () => envelope({ unlocked_topic_keys: [] })],
  [
    /^\/api\/courses\/(\d+)\/exam-mode\/analysis\/(\d+)\/questions$/,
    () => envelope(EXAM_QUESTIONS),
  ],
  [/^\/api\/courses\/(\d+)\/exam-mode\/analysis$/, () => envelope(EXAM_ANALYSIS)],
  [/^\/api\/courses\/(\d+)\/exam-mode\/plans\/(\d+)$/, () => envelope(EXAM_PLAN)],
  [/^\/api\/courses\/(\d+)\/exam-mode\/plans$/, () => envelope(EXAM_PLAN_LIST)],
  [/^\/api\/courses\/(\d+)\/exam-mode\/review-sheet$/, () => envelope(EXAM_REVIEW_SHEET)],
  [/^\/api\/courses\/(\d+)\/exam-roadmap$/, () => envelope({ roadmap: EXAM_ROADMAP, generated_output_id: 701 })],
  [/^\/api\/courses\/(\d+)\/documents/, () => envelope(DOCUMENTS)],
  [/^\/api\/courses\/(\d+)\/progress/, () => envelope(PROGRESS)],
  [/^\/api\/courses\/(\d+)\/settings/, () => envelope(SETTINGS)],
  [/^\/api\/courses\/(\d+)\/generation-jobs\/(\d+)\/retry$/, () => envelope({ job_id: GENERATION_JOB.id, status: 'queued' })],
  [
    /^\/api\/courses\/(\d+)\/generation-jobs\/(\d+)\/dismiss$/,
    () => {
      quizJobQueued = false
      return envelope({ ...GENERATION_JOB, dismissed_at: '2026-08-30T12:05:00Z' })
    },
  ],
  [/^\/api\/courses\/(\d+)\/generation-jobs\/(\d+)$/, () => envelope(GENERATION_JOB)],
  [/^\/api\/courses\/(\d+)\/generation-jobs$/, () => envelope(quizJobQueued ? [GENERATION_JOB] : [])],
  [
    /^\/api\/courses\/(\d+)\/quiz\/jobs$/,
    () => {
      quizJobQueued = true
      return envelope({ job_id: GENERATION_JOB.id, status: 'queued' })
    },
  ],
  [/^\/api\/courses\/(\d+)\/generated-outputs/, () => envelope([])],
  [/^\/api\/courses\/(\d+)\/conversations/, () => envelope([])],
  [
    /^\/api\/courses\/(\d+)\/quizzes\/(\d+)\/sessions$/,
    () => envelope({ session: QUIZ_SESSION, quiz: TIMED_QUIZ }),
  ],
  [
    /^\/api\/courses\/(\d+)\/quizzes\/(\d+)\/sessions\/(\d+)\/submit$/,
    () => envelope(ATTEMPT),
  ],
  [
    /^\/api\/courses\/(\d+)\/quizzes\/(\d+)\/sessions\/(\d+)\/answers\/(\d+)$/,
    () => envelope(QUIZ_SESSION),
  ],
  [
    /^\/api\/courses\/(\d+)\/quizzes\/(\d+)\/sessions\/(\d+)$/,
    () => envelope(QUIZ_SESSION),
  ],
  [/^\/api\/courses\/(\d+)\/quizzes\/9$/, () => envelope(TIMED_QUIZ)],
  [/^\/api\/courses\/(\d+)\/quizzes\/(\d+)\/attempts\/(\d+)$/, () => envelope(ATTEMPT)],
  [/^\/api\/courses\/(\d+)\/quizzes\/(\d+)\/attempts/, () => envelope(ATTEMPT)],
  [/^\/api\/courses\/(\d+)\/quizzes\/(\d+)$/, () => envelope(QUIZ)],
  [/^\/api\/courses\/(\d+)\/quizzes/, () => envelope([])],
  [/^\/api\/courses\/(\d+)\/quiz/, () => envelope({ ...CONTEXT, quiz: QUIZ })],
  [
    /^\/api\/courses\/(\d+)\/qa$/,
    () =>
      envelope({
        ...CONTEXT,
        answer: 'Breadth-first search settles vertices in order of edge count. [S1]',
        citations: [CITATION],
        conversation_id: 1,
      }),
  ],
  [
    /^\/api\/courses\/(\d+)\/ai-tutor$/,
    () =>
      envelope({
        ...CONTEXT,
        answer: 'Start by asking what a queue guarantees about order. [S1]',
        citations: [CITATION],
        conversation_id: 2,
      }),
  ],
  [
    /^\/api\/courses\/(\d+)$/,
    (match) => envelope(COURSES.find((course) => course.id === Number(match[1])) ?? COURSES[0]),
  ],
  [/^\/api\/courses\/?$/, () => envelope(COURSES)],
]

/**
 * Anything the specs do not cover answers 404 rather than falling through to a
 * real network request, so a screen that starts calling something new fails
 * loudly here instead of hanging.
 */
export async function stubApi(page: Page) {
  quizJobQueued = false
  await page.route('**/api/**', async (route: Route) => {
    const path = new URL(route.request().url()).pathname

    for (const [pattern, build] of ROUTES) {
      const match = path.match(pattern)
      if (match) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(build(match)),
        })
        return
      }
    }

    await route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ detail: `The browser suite has no fixture for ${path}` }),
    })
  })
}
