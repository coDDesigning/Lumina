"""Verifies that ON DELETE CASCADE works end to end on the content chain

Run from the repo root: python scripts/cascade_smoke.py
"""

from backend.app.database import Base, SessionLocal, engine
from backend.app.models import (
    Course,
    DocumentChunk,
    Progress,
    Quiz,
    QuizAttempt,
    QuizQuestion,
    Role,
    UploadedDocument,
    User,
)

Base.metadata.create_all(engine)

session = SessionLocal()

role = Role(name="student")
user = User(email="smoke@test.local", password_hash="not-a-real-hash", role=role)
course = Course(title="Smoke Course", owner=user)

doc = UploadedDocument(
    course=course, original_filename="notes.pdf", storage_path="/tmp/notes.pdf"
)

chunk = DocumentChunk(document=doc, course=course, chunk_index=0, text="hello world")

session.add(role)
session.commit()

print("chunks before delete:", session.query(DocumentChunk).count())

session.delete(course)
session.commit()

print("chunks after delete: ", session.query(DocumentChunk).count())
print("users still alive:", session.query(User).count())


# --- Second check: the quiz chain cascades too ---

# Rebuild a small tree: a fresh user, course, quiz, one question,
# one attempt, one progress row. (The first check deleted the old
# course, so we build anew.)
user2 = User(email="smoke2@test.local", password_hash="x", role=role)
course2 = Course(title="Quiz Course", owner=user2)
quiz = Quiz(title="Smoke Quiz", course=course2)
question = QuizQuestion(
    quiz=quiz,
    question_index=0,
    question_text="2 + 2 = ?",
    options=["3", "4", "5"],
    correct_option_index=1,
)
attempt = QuizAttempt(user=user2, quiz=quiz, score=1.0)
progress = Progress(user=user2, course=course2, completion=0.5)

session.add(user2)
session.commit()

print("questions before course delete:", session.query(QuizQuestion).count())

# Deleting the COURSE must destroy quiz -> question -> attempt and the
# progress row, through the database-level cascades, in one transaction.
session.delete(course2)
session.commit()

print("questions after course delete:", session.query(QuizQuestion).count())
print("attempts after course delete:", session.query(QuizAttempt).count())
print("progress after course delete:", session.query(Progress).count())

session.close()
