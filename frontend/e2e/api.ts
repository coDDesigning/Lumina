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
  topics: 'Graph Traversals, Shortest Paths, Minimum Spanning Trees',
  syllabus: '',
  description: 'Graphs, trees, and the algorithms over them.',
  created_at: '2026-08-01T09:00:00Z',
  updated_at: '2026-08-20T09:00:00Z',
}

const COURSES = [
  COURSE,
  { ...COURSE, id: 2, title: 'Linear Algebra', subject_area: 'Mathematics', topics: 'Eigenvalues' },
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

type Answer = [RegExp, (match: RegExpMatchArray) => unknown]

const ROUTES: Answer[] = [
  [/^\/api\/auth\/me$/, () => USER],
  [/^\/api\/auth\/login$/, () => ({ access_token: 'stub', token_type: 'bearer', user: USER })],
  [/^\/api\/auth\/register$/, () => envelope(USER)],
  [/^\/api\/users\/me\/credits$/, () => envelope(CREDITS)],
  [/^\/api\/users\/me\/credit-transactions/, () => envelope([])],
  [/^\/api\/models/, () => envelope(MODELS)],
  [/^\/api\/profile-knowledge/, () => envelope(KNOWLEDGE)],
  [/^\/api\/activity/, () => envelope([])],
  [/^\/api\/progress\/?$/, () => envelope(PROGRESS_SUMMARIES)],
  [/^\/api\/admin\/users/, () => envelope(ADMIN_USERS)],
  [/^\/api\/admin\/ai-costs/, () => envelope(AI_COSTS)],
  [/^\/api\/courses\/(\d+)\/documents/, () => envelope(DOCUMENTS)],
  [/^\/api\/courses\/(\d+)\/progress/, () => envelope(PROGRESS)],
  [/^\/api\/courses\/(\d+)\/settings/, () => envelope(SETTINGS)],
  [/^\/api\/courses\/(\d+)\/generated-outputs/, () => envelope([])],
  [/^\/api\/courses\/(\d+)\/conversations/, () => envelope([])],
  [/^\/api\/courses\/(\d+)\/quizzes\/(\d+)\/attempts\/(\d+)$/, () => envelope(ATTEMPT)],
  [/^\/api\/courses\/(\d+)\/quizzes\/(\d+)\/attempts/, () => envelope(ATTEMPT)],
  [/^\/api\/courses\/(\d+)\/quizzes\/(\d+)$/, () => envelope(QUIZ)],
  [/^\/api\/courses\/(\d+)\/quizzes/, () => envelope([])],
  [/^\/api\/courses\/(\d+)\/quiz/, () => envelope({ ...CONTEXT, quiz: QUIZ })],
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
