# Demo Script — Lumina MVP

Status: active.
References: SCRUM-177 (seed script), SCRUM-178 (pre-demo checklist), six-workstream
task breakdown, progress report 29 Aug 2026 (open-gap 6, risk 1).

---

## Overview

The demonstration is split into two segments with an explicit cut line.

| Segment | Budget | Purpose |
|---|---|---|
| **Core** (steps 1–9) | 16 min | The six-workstream MVP being assessed |
| **Bonus** (steps 10–12) | 4 min | Work built beyond the specification |

The bonus segment is **cuttable**. If the clock reaches 16 min with core
incomplete, skip directly to the closing statement. If the core finishes ahead of
schedule, show as much bonus as time allows.

Total slot: **20 min**.

---

## Prerequisites

Run the pre-demo checklist (SCRUM-178) before the first audience member arrives.
The checklist verifies:

- Backend is running (`/health/ready` returns `ready`).
- Worker process is running and polling.
- Frontend dev server is serving on `localhost:5173`.
- The seed script (SCRUM-177) has been executed and its entities exist.
- The browser is logged out and positioned on the landing page (`/`).
- The AI provider is reachable (a test completion returns within 5 s).
- A pre-generated study guide and a pre-generated quiz exist in the seed data
  for the fallback path.

Do **not** restate the checklist contents here; a presenter follows it from its
own document.

---

## Seed entities used

Every step that does not involve live generation or live upload uses a seeded
entity from SCRUM-177. The table below maps steps to their seed data.

| Step | Seeded entity | Purpose |
|---|---|---|
| 1 – Register | — | Fresh registration; no seed needed |
| 2 – Create course | — | Live creation; no seed needed |
| 3 – Upload | `demo_lecture.pdf` (a ≤ 5-page PDF in the repo's `fixtures/` or provided by the seed script) | The file being uploaded |
| 4 – Processing | — | Observing the live pipeline on the file from step 3 |
| 5 – Summary | Seed course `CS101-Demo` with one `ready` document and pre-generated study guide output `seed_study_guide_id` | Fallback if live generation stalls |
| 6 – Quiz | Seed course `CS101-Demo` with pre-generated quiz `seed_quiz_id` | Fallback if live generation stalls |
| 7 – Results | Seed quiz attempt `seed_attempt_id` on `seed_quiz_id` | Fallback for step 6 |
| 8 – Progress | Seed course `CS101-Demo` with quiz history populating per-topic mastery | Reads live data; seed provides the history |
| 9 – Admin | Seed admin account; seed regular users visible in the user list | Pre-seeded by SCRUM-177 |
| 10 – Exam roadmap | Seed course with `exam_date` and `course_topics` | Bonus; uses seed course |
| 11 – Flashcards | Seed course `CS101-Demo` | Bonus; headline mention only |
| 12 – Tutor / Credits | Seed course `CS101-Demo`; seed credit balance | Bonus; headline mention only |

---

## Live AI calls and fallbacks

Exactly **two** steps make a live AI call. Every other step reads stored data or
exercises non-AI paths.

### Live call 1 — Study guide generation (step 5)

**What happens:** The presenter clicks "Generate a study guide", the spinner
runs, and a markdown summary appears with source citations.

**Fallback:** If the provider takes longer than 15 s or returns an error, the
presenter says:

> "The provider is slower than usual today, so let me open one we prepared
> earlier."

Then navigate to `/courses/{courseId}/guides/{seed_study_guide_id}` to show the
pre-generated guide from seed data.

### Live call 2 — Quiz generation (step 6)

**What happens:** The presenter opens the quiz modal, selects 5 multiple-choice
questions, clicks generate. Questions appear and the presenter answers them.

**Fallback:** If generation stalls beyond 15 s, the presenter says:

> "I'll switch to a quiz we saved earlier so we can see the answering flow."

Then navigate to `/courses/{courseId}/practice/{seed_quiz_id}` to start the
pre-generated quiz.

---

## Core segment — step-by-step running order

### Step 1 — Register and sign in

| | |
|---|---|
| **Time** | 0:00 – 1:30 |
| **Budget** | 1 min 30 s |
| **Workstream** | WS-1: Authentication and user management |
| **Route** | `/register` → `/login` → redirect to `/dashboard` |
| **Seed data** | None |
| **Talking points** | |

1. Open the browser on `/`. Point out the landing page briefly — it
   communicates what the product does. Click **Get started**.
2. Fill in the registration form: name, email, password. Submit.
3. Note the redirect to the login page. Log in with the credentials just
   created.
4. Arrive at the empty dashboard. State: "This is a new account with no courses
   yet."

**Say which deliverable is being shown:**
> "This is the authentication system — registration, login, and JWT-based
> session management."

---

### Step 2 — Create a course

| | |
|---|---|
| **Time** | 1:30 – 3:00 |
| **Budget** | 1 min 30 s |
| **Workstream** | WS-2: Course management |
| **Route** | `/dashboard` (create dialog) |
| **Seed data** | None |
| **Talking points** | |

1. Click the **New course** button. The creation dialog opens.
2. Fill in:
   - **Name:** "Introduction to Computer Science"
   - **Subject area:** "Computer Science"
   - **Education level:** select "Undergraduate"
   - **Exam date:** a date two weeks from today
   - **Topics:** type and tag 3–4 topics, e.g., "Data Structures", "Algorithms",
     "Complexity", "Graphs"
3. Submit. The new course card appears on the dashboard with status
   `no_documents`.
4. Click the course card to enter the workspace.

**Say which deliverable is being shown:**
> "Course creation with structured metadata — subject, level, exam date, and a
> topic syllabus. The status system tracks readiness from here on."

---

### Step 3 — Upload a document

| | |
|---|---|
| **Time** | 3:00 – 4:30 |
| **Budget** | 1 min 30 s |
| **Workstream** | WS-3: Document upload and processing |
| **Route** | `/courses/{id}` (workspace page) |
| **Seed data** | `demo_lecture.pdf` |
| **Talking points** | |

1. In the workspace, click **Upload**. Select `demo_lecture.pdf`.
2. Choose material kind: "Lecture notes".
3. Upload completes. The document appears in the source list with status
   **Queued**.
4. Point out the validation: "The system checks file type and size before
   accepting it, and deduplicates by content hash."

**Say which deliverable is being shown:**
> "Document ingestion — upload with content validation, duplicate detection, and
> material classification."

---

### Step 4 — Show processing stages

| | |
|---|---|
| **Time** | 4:30 – 6:30 |
| **Budget** | 2 min |
| **Workstream** | WS-3: Document upload and processing |
| **Route** | `/courses/{id}` (workspace page, document row) |
| **Seed data** | None (live pipeline on uploaded file) |
| **Talking points** | |

1. The document row shows a progress indicator. As the worker processes the
   file, the status advances through named stages:
   - **Checking the file** (validating)
   - **Pulling out the text** (extracting_text)
   - **Reading the scans** (running_ocr) — appears only for image-heavy PDFs
   - **Describing the figures** (understanding_images) — appears only when
     diagrams are found
   - **Tidying it up** (cleaning_text)
   - **Splitting it up** (chunking)
   - **Indexing it** (generating_embeddings)
2. Wait for status to reach **Ready**. While waiting, explain: "Each stage is a
   discrete pipeline step. OCR and image understanding are conditional — they
   activate only when the document needs them. Embeddings are generated in the
   same transaction so a document is never 'ready' without being searchable."
3. The course status badge updates from `processing` to `ready`.

> **Timing note:** If the file is small, processing completes in under 30 s and
> you gain time. If it takes longer than 90 s, move on and say "It will finish
> in the background — let me switch to our prepared course so we can show
> generation."  Then log out and log in as the seed user with the seed course
> `CS101-Demo` already in `ready` state.

**Say which deliverable is being shown:**
> "The seven-stage document processing pipeline — from validation through text
> extraction, OCR, image description, cleaning, chunking, to vector embedding."

---

### Step 5 — Generate a study guide (LIVE AI CALL)

| | |
|---|---|
| **Time** | 6:30 – 9:00 |
| **Budget** | 2 min 30 s |
| **Workstream** | WS-4: AI-powered study guide generation |
| **Route** | `/courses/{id}` → study guide modal → `/courses/{id}/guides/{outputId}` |
| **Seed data** | Fallback: `seed_study_guide_id` |
| **Talking points** | |

1. Click the **Study Guide** action button. The generation modal opens.
2. Optionally select a topic focus (e.g. "Data Structures") or leave it on
   "Every topic" to demonstrate breadth.
3. Click **Generate**. The spinner shows elapsed seconds.
4. **If generation succeeds** (expected: 8–15 s): the rendered markdown guide
   appears with source citations ([S1], [S2], etc.) linking back to specific
   document pages.
   - Point out the citations: "Every claim traces back to a page in the uploaded
     material."
   - Point out `retrieval_narrowed` in the context badge: "The retriever chose
     the most relevant chunks, not the whole document."
5. **If generation stalls:** follow the fallback script. Open the seed guide.

**Say which deliverable is being shown:**
> "AI-generated study guides with per-claim source citations, built from
> semantically retrieved course material."

---

### Step 6 — Generate and solve a quiz (LIVE AI CALL)

| | |
|---|---|
| **Time** | 9:00 – 12:00 |
| **Budget** | 3 min |
| **Workstream** | WS-5: AI-generated quiz and assessment |
| **Route** | `/courses/{id}` → quiz modal → `/courses/{id}/practice/{quizId}` |
| **Seed data** | Fallback: `seed_quiz_id` |
| **Talking points** | |

1. Click the **Practice Quiz** action button. The quiz setup modal opens.
2. Configure:
   - **Questions:** 5
   - **Types:** Multiple choice (keep it fast for the demo)
   - **Difficulty:** Medium
   - **Topic:** "Algorithms" (or leave broad)
3. Click **Generate**. Wait for the questions to arrive.
4. **If generation succeeds:** the quiz attempt page opens with 5 questions.
   - Answer 3 correctly and 2 incorrectly (to produce a visible score spread
     for the progress step).
   - Click **Submit**.
5. **If generation stalls:** follow the fallback script. Open the seed quiz.

**Say which deliverable is being shown:**
> "Quiz generation from course material — configurable question count, type,
> difficulty, and topic scope. The generated questions are validated against the
> requested parameters before being stored."

---

### Step 7 — Show the result screen

| | |
|---|---|
| **Time** | 12:00 – 13:30 |
| **Budget** | 1 min 30 s |
| **Workstream** | WS-5: AI-generated quiz and assessment |
| **Route** | `/courses/{id}/practice/{quizId}/attempts/{attemptId}` |
| **Seed data** | Fallback: `seed_attempt_id` |
| **Talking points** | |

1. After submission, the result screen appears automatically.
2. Point out:
   - **Score:** "3 out of 5 — 60%."
   - **Per-question feedback:** correct answer shown alongside the student's
     answer.
   - **Source citations** on each question linking to the material.
3. Say: "The grading distinguishes question types. Multiple choice and
   true/false are scored by option index. Short answers check normalized
   variants. Open-ended answers go through the AI for evaluation — and an answer
   the AI cannot score is recorded as ungraded, never wrong."

**Say which deliverable is being shown:**
> "Quiz results with per-question grading, source citations, and an honest
> scoring model that distinguishes ungraded from incorrect."

---

### Step 8 — Show the progress dashboard

| | |
|---|---|
| **Time** | 13:30 – 15:00 |
| **Budget** | 1 min 30 s |
| **Workstream** | WS-6: Progress tracking and analytics |
| **Route** | `/courses/{id}/progress` |
| **Seed data** | Seed quiz history on `CS101-Demo` |
| **Talking points** | |

1. Navigate to the course progress page (click the progress link in the course
   header).
2. Point out:
   - **Overall score** and **completion** percentage.
   - **Per-topic mastery:** each declared topic shows a mastery badge —
     `Mastered`, `In Progress`, or `Needs Review`.
   - **Study time** accumulated across attempts.
   - **Weak topics** are visually highlighted for the student.
3. Explain the status derivation: "Status is derived live from quiz attempts.
   `mastered` means the average score, rounded to the percentage the card
   displays, meets the threshold. A course with no attempts shows null, not
   zero, because zero would claim something the data does not support."
4. Navigate back to `/dashboard` and point out the course cards: "The dashboard
   is a single-request read — one summary per course, showing the same status."

**Say which deliverable is being shown:**
> "Progress tracking — per-topic mastery, derived status, and a dashboard
> summary, all computed at read time from quiz history."

---

### Step 9 — Show the administrator view

| | |
|---|---|
| **Time** | 15:00 – 16:30 |
| **Budget** | 1 min 30 s |
| **Workstream** | WS-1: Authentication and user management (admin role) |
| **Route** | `/admin` |
| **Seed data** | Seed admin account, seed regular users |
| **Talking points** | |

1. Log out of the student account. Log in as the seed administrator.
2. Navigate to `/admin`.
3. Point out:
   - **User list** with search, role filter, and status filter.
   - **Role management:** the ability to promote or demote users (but not the
     bootstrap admin or yourself).
   - **Ban/unban** toggle.
   - **User courses:** click a user to see their courses (read-only;
     administrators can read any course but write only their own).
4. Mention but do not deep-dive: "The admin page also shows AI cost reporting
   and credit management, which we'll touch on in the bonus segment if time
   permits."

**Say which deliverable is being shown:**
> "Administrator controls — user management, role assignment, and the guard
> that prevents the bootstrap administrator from being locked out."

---

### CORE COMPLETE — 16:30

State:
> "That covers every deliverable in the six-workstream specification:
> authentication, course management, document processing, study guide
> generation, quiz and assessment, and progress tracking. What follows is work
> we built beyond the specification."

---

## Bonus segment — cuttable

> **Rule:** If the clock is at or past 18:00 when you reach the bonus, skip
> steps 11–12 and go to the closing statement. If the clock is past 16:30 but
> before 18:00, start with step 10 and cut as needed.

---

### Step 10 — Exam Mode roadmap (bonus)

| | |
|---|---|
| **Time** | 16:30 – 18:30 |
| **Budget** | 2 min |
| **Workstream** | Beyond spec (SCRUM-165, 166, 167, 169) |
| **Route** | `/courses/{id}` → Exam Roadmap modal |
| **Seed data** | Seed course with `exam_date` and `course_topics` |
| **Talking points** | |

1. Open the seed course (or the course from the core demo if it has an exam date
   and topics).
2. Click the **Exam Roadmap** button. The roadmap generates (no AI call — pure
   scheduling logic).
3. Show the day-by-day plan:
   - Topics allocated per day based on priority (weakness × importance).
   - Final review on exam day.
   - Materials resolved per topic with document and page references.
4. Say: "Exam Mode spans twenty service modules — topic ranking, scheduling,
   roadmaps, mock exams, similar questions, staleness tracking. This single
   screen shows the planning output."

---

### Step 11 — Flashcards (headline only)

| | |
|---|---|
| **Time** | 18:30 – 19:00 |
| **Budget** | 30 s |
| **Workstream** | Beyond spec (SCRUM-141) |

Mention only — do not navigate:
> "Flashcard generation uses the same retrieval pipeline as study guides. A
> student generates a deck from course material, studies it with a flip
> interaction, and the decks are saved for revisiting."

---

### Step 12 — AI Tutor and Credits (headline only)

| | |
|---|---|
| **Time** | 19:00 – 19:30 |
| **Budget** | 30 s |
| **Workstream** | Beyond spec (SCRUM-110, 111, 112) |

Mention only — do not navigate:
> "The AI tutor is a conversational interface scoped to a course's material,
> with persisted conversation history. Course Q&A is a second conversation type.
> Both are metered by a credit ledger — each generation deducts from a balance,
> refunds on failure, and the credit lifecycle is documented in `docs/credits.md`.
> The admin page includes credit management and AI cost reporting."

---

### Closing statement — 19:30–20:00

> "Lumina processes uploaded study material through a seven-stage pipeline, uses
> semantic retrieval to generate study guides and quizzes grounded in that
> material, tracks progress per topic, and gives administrators visibility into
> the platform. Everything beyond that — Exam Mode, flashcards, the tutor, and
> the credit system — is additional work we've completed but that sits outside
> the assessed specification. Questions?"

---

## Screens deliberately not shown

| Screen | Reason |
|---|---|
| `/account` (profile settings) | User profile editing is functional but not part of the assessed deliverables. Showing it dilutes the core narrative without satisfying a workstream. |
| `/account/background` (profile knowledge) | Profile knowledge shapes prompt context but is not in the six-workstream spec. It would require explaining the prompt template system, consuming 2+ min. |
| `/account/ai` (AI preferences) | Model selection preferences are a configuration detail, not a deliverable. |
| `/account/appearance` (theme toggle) | Visual polish, not assessed. |
| `/activity` (activity feed) | The activity feed is a cross-course history view. It is useful but duplicates narrative already covered by the progress dashboard. |
| Course settings (`/courses/{id}/settings`) | Course editing is a CRUD operation already demonstrated by creation. Showing it separately adds time without a new workstream. |
| Course Q&A conversation | Covered by the headline mention of the tutor in the bonus. Showing two conversation types doubles the time. |
| Reverse quiz | Not in the six-workstream spec. |
| Prompt generator | Beyond spec; a power-user feature. |
| Landing page deep-dive | Visible at the start but not narrated in detail — it is marketing, not a deliverable. |

---

## Rehearsal log

The script was rehearsed end-to-end against the pre-demo checklist (SCRUM-178)
on the date below. Measured timings replace the initial estimates.

| Step | Budgeted | Measured | Δ | Notes |
|---|---|---|---|---|
| 1 – Register & sign in | 1:30 | — | — | Fill after rehearsal |
| 2 – Create course | 1:30 | — | — | |
| 3 – Upload | 1:30 | — | — | |
| 4 – Processing stages | 2:00 | — | — | Depends on file size; 5-page PDF typically < 30 s |
| 5 – Study guide (live) | 2:30 | — | — | Provider latency is the variable |
| 6 – Quiz (live) | 3:00 | — | — | Answering 5 MC questions takes ~60 s |
| 7 – Results | 1:30 | — | — | |
| 8 – Progress | 1:30 | — | — | |
| 9 – Admin | 1:30 | — | — | |
| **Core total** | **16:30** | **—** | | |
| 10 – Exam roadmap | 2:00 | — | — | |
| 11 – Flashcards | 0:30 | — | — | Headline only |
| 12 – Tutor / Credits | 0:30 | — | — | Headline only |
| Closing | 0:30 | — | — | |
| **Total** | **20:00** | **—** | | |

> **Action required:** Perform the rehearsal, fill the "Measured" column with
> actual elapsed times, compute the delta, and adjust budgets to match reality.
> Record the rehearsal date below.

**Rehearsal date:** _(not yet performed)_
**Rehearsal performer:** _(name)_
**Environment:** _(self_hosted local / hosted staging)_

---

## Quick reference — workstream mapping

| Workstream | Core steps | What is shown |
|---|---|---|
| WS-1: Authentication & user management | 1, 9 | Registration, login, JWT session, admin role guard, user list, ban/unban, role change |
| WS-2: Course management | 2 | Course creation with metadata, topics, exam date |
| WS-3: Document upload & processing | 3, 4 | Upload with validation, 7-stage pipeline, status progression |
| WS-4: Study guide generation | 5 | AI-generated guide with source citations, semantic retrieval |
| WS-5: Quiz & assessment | 6, 7 | Quiz generation, configurable parameters, attempt, grading, results |
| WS-6: Progress tracking | 8 | Per-topic mastery, derived status, dashboard summary |

---

## Quick reference — bonus mapping

| Feature | SCRUM tickets | Step |
|---|---|---|
| Exam Mode (roadmap, schedule, ranking) | SCRUM-165, 166, 167, 169 | 10 |
| Flashcards | SCRUM-141 | 11 |
| Credit ledger & cost reporting | SCRUM-110, 111, 112 | 12 |
| AI Tutor / Course Q&A | — | 12 |
| Profile knowledge | — | Not shown |
| Reverse quiz | — | Not shown |
| Prompt generator | — | Not shown |
