# Vector Storage Architecture

Status: accepted (SCRUM-68). Supersedes the reserved, unused vector plumbing that
preceded it.

## Decision

Lumina stores one embedding per current document chunk, in a vector backend chosen
by the database the deployment runs on.

| Deployment | Relational store | Vector store |
| --- | --- | --- |
| Hosted, or any PostgreSQL `DATABASE_URL` | PostgreSQL | `chunk_embeddings` table with a pgvector `vector(768)` column and an HNSW cosine index |
| Self-hosted (SQLite default) | SQLite | ChromaDB `PersistentClient` rooted at `CHROMA_PERSIST_DIRECTORY` |

`VECTOR_BACKEND` selects the store. It defaults to `pgvector` on a PostgreSQL
`DATABASE_URL` and `chroma` otherwise. Choosing `pgvector` without a PostgreSQL
database fails at startup, because the `vector` type exists only in PostgreSQL.

Both backends implement the same `VectorStore` interface in
`services/vector_store.py`. No caller knows which one is configured.

## Why the split

SQLite is the default and only fully supported self-hosted database, and it cannot
host a pgvector column. A pgvector-only decision would therefore have forced
PostgreSQL onto the self-hosted path, or left self-hosted deployments with no
semantic retrieval at all. A Chroma-only decision would have put vectors outside
the transactional boundary even where PostgreSQL can hold them safely.

The split keeps each deployment on the strongest option available to it, at the
cost of two implementations and one genuine consistency caveat, recorded below.

## Consistency model

Embeddings are generated in the worker parent process, in memory, before anything
is published, and written inside the transaction that publishes the chunks:

```
chunking (extraction subprocess ends)
  -> processing_stage = generating_embeddings
  -> embed every chunk text                       (network, parent process)
  -> complete_job transaction:
       delete the previous document_chunks
       insert the new document_chunks
       flush                                      (chunk ids now exist)
       vector_store.replace_document_vectors(...)
       uploaded_documents.status = 'ready'
     COMMIT
```

A document therefore never reaches `ready` because chunks exist; it reaches `ready`
only once its vectors are stored too.

**pgvector** writes through the caller's SQLAlchemy `Session`, so chunks, vectors,
and `ready` commit or roll back together. There is no window.

**Chroma** cannot join that transaction. It is written after the flush and before
the commit, which leaves exactly one failure window: Chroma accepts the write and
the relational commit then fails. The result is vectors whose `chunk_id` no longer
resolves. Three things contain it:

- `replace_document_vectors` deletes every vector for the document before writing
  the new set, so any retry converges rather than accumulating.
- Retrieval joins vectors back to `document_chunks`, so an unresolvable vector
  returns nothing.
- `python -m workers.embedding_backfill --prune-orphans` removes vectors whose
  chunk is gone.

The reverse ordering was rejected: writing vectors after the commit would let a
document be `ready` with no vectors, which is the failure mode this ticket exists
to prevent.

## Vector identity and metadata

The vector id is the relational `document_chunks.id`. Every vector carries:

| Field | Purpose |
| --- | --- |
| `chunk_id` | resolves the vector back to its exact chunk |
| `document_id` | document-scoped replacement and deletion |
| `course_id` | course-scoped retrieval filtering and deletion |
| `chunk_index` | ordering and citation |
| `embedding_provider`, `embedding_model` | attribution; vectors from different models are not comparable |

`course_id` is what keeps retrieval inside the caller's own workspace. Both search
implementations take it as a mandatory argument, so a similarity search never asks
for "the nearest vectors in the database" without a scope.

In PostgreSQL these are real columns on `chunk_embeddings`, with a composite foreign
key `(chunk_id, document_id, course_id)` into `document_chunks` so the denormalized
values cannot drift. In Chroma they are collection metadata on each record.

## Similarity metric

Cosine, on both backends, and it is the same decision in three places that must
agree: the stored vectors, the index (`vector_cosine_ops` for HNSW; `hnsw:space =
cosine` for the Chroma collection), and the similarity search in
`services/vector_store.py`.

HNSW was chosen over IVFFlat because it handles incremental inserts without the
train-then-populate workflow IVFFlat needs, and documents arrive one at a time.

## Dimensions

`EMBEDDING_DIMENSIONS = 768`, fixed in `backend/app/models.py`. It is a schema
constant, not a setting: a pgvector column and its index are declared at one width.
`nomic-embed-text` and Gemini `text-embedding-004` both produce 768.

A provider returning a different width fails permanently with
`EMBEDDING_DIMENSION_MISMATCH` rather than storing something unusable.

To move to a model of another width: change the constant, write a migration that
alters the column and rebuilds the index, and re-embed everything. Vectors of two
different models must never share an index — that is what `embedding_model` on each
record exists to make detectable.

## Lifecycle

| Event | Behaviour |
| --- | --- |
| Process | Chunks and vectors are inserted in one transaction; `ready` follows. |
| Reprocess | The document's vectors are deleted before the new set is written, so a shrinking chunk set leaves nothing stale. |
| Embedding failure | Nothing is published. The job records a classified error and either requeues or fails permanently. |
| Document delete | Vectors are removed while the document is still tombstoned, before its row is deleted. |
| Course delete | Vectors are removed before the course row is deleted. |
| Course purge | `python -m workers.course_purge` reruns that same deletion for every tombstoned course; runs periodically in the background worker every `COURSE_PURGE_INTERVAL_SECONDS`. |
| Backfill | Reconciles missing vectors; safe to rerun; runs periodically in the background worker every `EMBEDDING_BACKFILL_INTERVAL_SECONDS`; `--prune-orphans` removes vectors whose chunk is gone. |

On PostgreSQL, `ON DELETE CASCADE` would already remove vectors with their chunks.
The deletion calls are still issued explicitly so both backends behave identically
and are covered by the same tests.

Deletion runs before the owning row disappears, deliberately. If it fails, the
tombstone remains and the operation is retryable; the reverse order could leave
deleted content semantically searchable, which is the worse outcome.

## Error classification

`services/document_embedding.py` maps provider and store failures onto stable codes
recorded on the processing job. Retryable failures requeue with the existing capped
backoff; permanent ones fail the document.

| Code | Retryable |
| --- | --- |
| `EMBEDDING_TIMEOUT` | yes |
| `EMBEDDING_PROVIDER_UNAVAILABLE` | yes |
| `EMBEDDING_RATE_LIMITED` | yes |
| `VECTOR_PERSISTENCE_FAILED` | yes |
| `EMBEDDING_INVALID_RESPONSE` | no |
| `EMBEDDING_DIMENSION_MISMATCH` | no |
| `EMBEDDING_CONFIGURATION_INVALID` | no |
| `VECTOR_STORE_UNAVAILABLE` | no |

Provider exception text never reaches a response; only these codes and their fixed
public messages do.

## Persistence

**PostgreSQL** — vectors are ordinary rows and survive restarts with the database.
The `vector` extension must be available; the migration runs
`CREATE EXTENSION IF NOT EXISTS vector`, which needs a role permitted to create
extensions. A managed PostgreSQL that does not offer pgvector cannot use this
backend and should run `VECTOR_BACKEND=chroma`.

**Chroma** — `CHROMA_PERSIST_DIRECTORY` must be durable storage. The supported
container path already puts it at `/data/chroma` on the `lumina-data` volume.
Losing that directory does not lose data: `python -m workers.embedding_backfill`
rebuilds it from the chunks, at the cost of re-embedding.

Chroma's `PersistentClient` is a local store backed by SQLite, and two processes
already write to it: the document worker on the embedding stage, and the API
process on document deletion and course hard deletion. Both open the same
directory. Deletions are rare and idempotent, and the worst observed outcome is a
vector that outlives its chunk, which `--prune-orphans` clears - but this is a
known limitation, not a designed-for topology. Moving Chroma writes behind the
worker, or onto Chroma's server mode, is the way out if deletion and embedding
ever contend in practice. The pgvector backend has no equivalent concern because
every writer goes through PostgreSQL.

## Schema on both databases

`chunk_embeddings` is created on SQLite as well as PostgreSQL, with a packed
float32 `BLOB` in place of the `vector` column (`EmbeddingVector` in
`backend/app/models.py` picks the type per dialect, the same way `UTCDateTime`
does). Keeping the table uniform means the schema contract and drift tests do not
have to special-case a dialect, and the pgvector store's lifecycle logic is
exercisable without a PostgreSQL server. On a SQLite deployment using the Chroma
backend the table simply stays empty.

The HNSW index is PostgreSQL-only. It is declared on the model with
`ddl_if(dialect="postgresql")` so `create_all` skips it elsewhere, and
`alembic/env.py` filters it out of autogenerate on other dialects so
`alembic check` stays honest on both.

## Retrieval

Both stores expose `search(session, *, course_id, query_embedding, limit)`, which
returns the `limit` nearest chunks of exactly that course, ranked best-first as a
`SearchResult` with a `similarity` in `[0, 1]` (cosine distance converted via
`1 - distance`):

- **pgvector** runs `embedding <=> CAST(:query AS vector)` inside a `WHERE
  course_id = :course_id`, so the HNSW index and the course filter stay in one
  statement. pgvector 0.8+ iterative scan is enabled transaction-locally with
  strict ordering and a bounded scan budget so nearer vectors in other courses
  cannot starve the requested course. The method refuses to run on a
  non-PostgreSQL connection.
- **Chroma** issues a collection query with `where={"course_id": course_id}` and
  the collection's cosine space; the caller's session is used only for validation.

`services/semantic_retrieval.py` is the single retrieval entry point. It embeds
the query with `EmbeddingProvider.embed_query`, validates the width, searches, and
resolves the hits back to `document_chunks.text` in one query. A vector whose
chunk row is gone — the known Chroma replication window — is skipped, and so is a
chunk with no text. Retrieval therefore degrades gracefully instead of surfacing
stale metadata.

The scope is mandatory by construction: `course_id` is a required keyword argument
at every layer, so no caller can ask for a cross-course search. Embedding and
store failures propagate as `EmbeddingError` and `VectorStoreError` subclasses for
the calling feature to classify.

## Retrieval-backed material

`services/semantic_retrieval.py` ranks; it does not assemble. Turning ranked
chunks into prompt material is `services/retrieval_material.py`, which
`load_retrieved_material(db, course_id, *, query, limit, min_similarity,
max_characters, ...)` owns:

1. Rank the course's chunks against the query, bounded by `limit`. If ranking
   returns nothing at all, the course holds no vectors, so raise
   `MaterialNotIndexedError` rather than a relevance miss.
2. Discard anything below `min_similarity`. If nothing survives, raise
   `NoRelevantMaterialError` — **the provider is never called and no row is
   written**.
3. Fill the character budget in similarity order, so the budget is never spent on
   the least relevant material.
4. Emit the retained chunks in corpus order, so the prompt reads as prose.

The similarity floor lives here rather than in `search`, so neither backend has to
implement it and the ranking contract stays narrow. The module reads no settings:
the calling feature supplies every bound, which keeps each remaining migration a
one-line change.

An indexing gap and a relevance miss are deliberately different errors. A course
whose documents are `ready` but never embedded matches nothing no matter what is
asked of it, so answering it with "try a broader topic focus" sends the student
after a problem they cannot reach; the fix is `python -m workers.embedding_backfill`
or reprocessing, and the message says so.

Embedding and store failures are translated here into `MaterialRetrievalError` and
its timeout and rate-limit subclasses, which `utils/ai_errors.py` maps to curated
responses. **There is no fallback to whole-corpus assembly.** A retrieval failure
fails the request; widening it silently would mean the feature was never really
retrieval-backed.

## Feature wiring
 
Study guide, quiz, flashcard, AI tutor, and course Q&A generation all read
retrieved material through `services/retrieval_material.py`. Whole-corpus assembly
via `services/course_material.py` remains in use only for the profile-knowledge
assembly helper.

