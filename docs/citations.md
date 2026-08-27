# Source Citations

Status: accepted (SCRUM-160).

## Decision

Generated learning content carries per-claim source attribution back to the
student's own documents and pages. A study-guide point, a quiz question, a Q&A
answer, and a Tutor reply can each name the passages they were drawn from, and
a citation the model invented is removed rather than rendered.

## The citation key

Retrieval supplies each passage to the prompt behind a header:

```
[S1] (Lecture 4, p. 12)
Binary search halves the range each step.

[S2] (Lecture 4, pp. 13-14)
...
```

The key is `S<n>`, 1-based, assigned in emission order by
`services/retrieval_material.py` in the same loop that writes the material
text. Key to chunk is therefore a bijection by construction, and there is no
second place that can mint a key. It is deliberately not derived from
`chunk_id`: that is unstable across reprocessing and would leak database
identifiers into model-facing text.

A key is meaningful only inside one prompt. That is why a persisted citation
stores the resolved document and page rather than the key alone, and why
`ConversationService.format_history` strips markers out of earlier turns: a
stale `[S1]` would resolve against the *new* turn's map and silently name the
wrong passage.

## How each feature carries them

| Feature | Form | Stored in |
| --- | --- | --- |
| Study guide | `citations` array on each cited field | `generated_outputs.content` |
| Quiz | `citations` array per question | `quiz_questions.citations` |
| Course Q&A | inline `[S1]` markers in the answer text | `conversation_messages.citations` |
| AI Tutor | inline `[S1]` markers in the answer text | `conversation_messages.citations` |
| Flashcards | none | — |
| Exam Mode topics | `citations` array per discovered topic | `exam_topic_candidates.citations` |
| Past exam paper questions | `citations` array per question | `past_exam_questions.citations` |

Flashcards opt out at the retrieval seam: `load_retrieved_material` takes a
required `include_citations` flag, so a card back can never pick up a stray
`[S3]` and the flashcard template needed no change.

Exam Mode leans on citations harder than the other features, because they are
not only attribution there. A transcribed past exam question takes its source
document and page range from the first citation that resolves to a selected
`past_exam` document, since a model cannot reliably know a PDF's own page
numbering. A question whose citations resolve to no past exam is stored with a
null document and null pages rather than being attributed to a paper it may not
have come from, and it is left out of the past-exam frequency signal entirely:
evidence a reader cannot go and check must not move a ranking.

The study-guide citable set is `summary`, `key_points`, `prerequisites`,
`learning_objectives`, `important_terms`, `common_mistakes`, and
`exam_tips.lecture_based`. `exam_tips.ai_suggestions` stays plain strings,
because the UI already labels it as not coming from the material.

## Validation

`services/citations.py` is the only place a key becomes a citation.
`resolve_citations` ignores non-strings, normalises `s3` / `[S3]` / `S03` to
`S3`, **drops any key not in the supplied map**, deduplicates by document and
page, and caps a single claim at `MAX_CITATIONS_PER_CLAIM`. For Q&A and Tutor,
`sanitize_citation_markers` does the same and additionally deletes the
unresolved marker from the answer text, repairing the spacing it leaves.

A bracket is treated as a citation group only when every comma-separated part
is a key, so `[see figure 2]` and `[1]` pass through untouched.

`document_label` derives the display name from `original_file_name`
(`lecture-04.pdf` → `Lecture 4`). It deletes `[`, `]`, `\r` and `\n` first:
the file name is student-supplied and reaches model-facing text, so
`evil[S1].pdf` must not be able to forge a key.

## What this does not guarantee

The validator catches a **fabricated** key. It cannot catch a **misattributed**
one — a model that writes `[S3]` for a claim that actually came from S7 produces
a citation that is resolvable and wrong. Keep the per-claim cap low, keep the
UI copy at "Sources" rather than "verified", and treat the dropped-key count in
the logs as the signal that a prompt version has regressed. Verifying a claim
against the passage it cites needs a retrieval call per claim and is out of
scope here.

## Persistence and reopen

Citations are stored denormalized: document id, label, and page range. Reopening
re-resolves nothing, so a stored output renders the same sources without a
provider call and without a join, and a later rename cannot retroactively
rewrite what a past generation actually read. A citation whose document has
since been deleted still renders — it remains a true statement about the past.
For that reason no citation is a link: `document_id` is kept as a hint for a
future affordance, not used as one today.

Both read paths are permissive, mirroring `parse_correct_answer`: an unreadable
citation document yields `[]` and a warning naming only the row id.

## Privacy

Citation keys, labels, and text never reach logs or usage telemetry.
`resolve_citations` logs a **count** of dropped keys and nothing else, the
`AiUsageLog` column set is unchanged, and `_ALLOWED_FIELDS` in
`backend/app/observability.py` is pinned by a test so a citation cannot reach a
log record through a new structured field. `tests/test_privacy_telemetry.py`
embeds a marker in the file name as well as the chunk, because the label is
derived from the file name.

## Cost

Each passage header costs roughly 30 characters of the material budget, so a
citing feature reaches slightly less text at the same `*_MATERIAL_MAX_CHARS`
than it did before. `load_retrieved_material` charges the header explicitly, so
the bound still holds exactly; the effect is that a course that only just fitted
may now report `context_truncated`.

How much this matters depends on whether the budget binds at all. At
`RETRIEVAL_CHUNK_LIMIT=24` and `DOCUMENT_CHUNK_SIZE_CHARACTERS=1200`, material
tops out near 29,000 characters, so a budget of 120,000 never binds and the
headers cost nothing. A deployment that raised the chunk limit, or lowered the
budget, is where the overhead shows: at the 200-chunk maximum the headers are
about 5% of a 120,000 budget. The four citing features therefore default to
`DEFAULT_CITED_MATERIAL_MAX_CHARACTERS` (126,000) while flashcards keep
`DEFAULT_MATERIAL_MAX_CHARACTERS` (120,000), because flashcards emit no headers
and widening theirs would only buy them more material than they had before.
