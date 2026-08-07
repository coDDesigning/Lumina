import os
import sys

# Ensure backend can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import Session
from backend.app.database import engine
from backend.app.models import User, Role, Course, UploadedDocument, DocumentChunk
from backend.app.vector_store import get_vector_store

def run_smoke_test():
    print("Starting VectorStore smoke test...")
    vs = get_vector_store()
    
    with Session(engine) as session:
        # Create a user and a role
        role = session.query(Role).filter_by(name="student").first()
        if not role:
            role = Role(name="student")
            session.add(role)
            session.commit()
            
        # Ensure user doesn't already exist from a previous failed run
        user = session.query(User).filter_by(email="vector_test@example.com").first()
        if not user:
            user = User(email="vector_test@example.com", password_hash="hash", role_id=role.id)
            session.add(user)
            session.commit()
        
        # Clean up any existing courses for this user to start fresh
        session.query(Course).filter_by(owner_id=user.id).delete()
        session.commit()
        
        # Create two courses
        course1 = Course(title="Biology 101", owner_id=user.id)
        course2 = Course(title="History 101", owner_id=user.id)
        session.add_all([course1, course2])
        session.commit()
        
        # Create a document for each course
        doc1 = UploadedDocument(course_id=course1.id, original_filename="biology.pdf", storage_path="/tmp/bio.pdf")
        doc2 = UploadedDocument(course_id=course2.id, original_filename="history.pdf", storage_path="/tmp/hist.pdf")
        session.add_all([doc1, doc2])
        session.commit()
        
        # Create chunks
        chunks_c1 = [
            DocumentChunk(document_id=doc1.id, course_id=course1.id, chunk_index=0, text="Biology is the study of life."),
            DocumentChunk(document_id=doc1.id, course_id=course1.id, chunk_index=1, text="The mitochondria is the powerhouse of the cell.")
        ]
        
        chunks_c2 = [
            DocumentChunk(document_id=doc2.id, course_id=course2.id, chunk_index=0, text="The French Revolution began in 1789."),
            DocumentChunk(document_id=doc2.id, course_id=course2.id, chunk_index=1, text="Napoleon Bonaparte was a French military and political leader.")
        ]
        
        session.add_all(chunks_c1 + chunks_c2)
        session.commit()
        
        # Add to vector store
        vs.add_chunks(chunks_c1 + chunks_c2)
        print("Added chunks to vector store.")
        
        # Test 1: Isolated Search
        # Let's search for "leader" in course 2
        results_c2 = vs.search("leader", course2.id)
        print(f"Search results for 'leader' in Course 2: {results_c2}")
        
        # It should return a chunk from Course 2
        assert len(results_c2) > 0, "Expected to find results in Course 2"
        assert chunks_c2[1].id in results_c2, "Expected to find the Napoleon chunk in Course 2"
        
        # If we search for "powerhouse" in Course 2, it should NOT return chunks from Course 1
        results_c2_bio = vs.search("powerhouse", course2.id)
        for cid in results_c2_bio:
            assert cid not in [c.id for c in chunks_c1], f"Found Course 1 chunk {cid} inside Course 2 search!"
            
        print("Course isolation verified.")
        
        # Test 2: Deletion
        vs.delete_course(course1.id)
        
        # Search for biology in course 1
        results_c1 = vs.search("biology", course1.id)
        assert len(results_c1) == 0, f"Course 1 chunks should be deleted, got {results_c1}"
        print("Course deletion verified.")
        
        # Clean up database
        session.delete(user)
        session.commit()
        print("Database cleaned up.")

if __name__ == "__main__":
    run_smoke_test()
