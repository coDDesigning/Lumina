"""Verifies that ON DELETE CASCADE works end to end on the content chain

Run from the repo root: python scripts/cascade_smoke.py
"""

from backend.app.database import Base, SessionLocal, engine
from backend.app.models import Course, DocumentChunk, Role, UploadedDocument, User

Base.metadata.create_all(engine)

session = SessionLocal()

role = Role(name="student")
user = User(email ="smoke@test.local", password_hash = "not-a-real-hash",role = role)
course = Course(title= "Smoke Course", owner = user)

doc = UploadedDocument(course = course, original_filename = "notes.pdf", storage_path = "/tmp/notes.pdf")

chunk = DocumentChunk(document = doc, course = course, chunk_index = 0, text= "hello world")

session.add(role)
session.commit()

print("chunks before delete:", session.query(DocumentChunk).count())

session.delete(course)
session.commit()

print("chunks after delete: ", session.query(DocumentChunk).count())
print("users still alive:", session.query(User).count())

session.close()