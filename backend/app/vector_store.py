from abc import ABC, abstractmethod
from typing import Sequence
import chromadb
import psycopg2
from chromadb.config import Settings as ChromaSettings
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer

from backend.app.models import DocumentChunk
from backend.app.config import settings

class VectorStore(ABC):
    @abstractmethod
    def add_chunks(self, chunks: Sequence[DocumentChunk]) -> None:
        """Add document chunks to the vector store."""
        pass

    @abstractmethod
    def search(self, query: str, course_id: int, top_k: int = 5) -> list[int]:
        """
        Search for relevant chunks within a specific course.
        Returns a list of chunk IDs (the database primary keys).
        """
        pass

    @abstractmethod
    def delete_course(self, course_id: int) -> None:
        """Remove all chunks associated with a course_id from the vector store."""
        pass


class ChromaVectorStore(VectorStore):
    def __init__(self, persist_directory: str):
        # We use PersistentClient which saves to disk
        self.client = chromadb.PersistentClient(path=persist_directory)
        
        # For our MVP, we use the default embedding function provided by ChromaDB 
        # (all-MiniLM-L6-v2) which downloads models on first run.
        self.collection = self.client.get_or_create_collection(name="lumina_chunks")

    def add_chunks(self, chunks: Sequence[DocumentChunk]) -> None:
        if not chunks:
            return
            
        ids = [str(chunk.id) for chunk in chunks]
        documents = [chunk.text for chunk in chunks]
        metadatas = [{"course_id": chunk.course_id, "document_id": chunk.document_id} for chunk in chunks]
        
        self.collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas
        )

    def search(self, query: str, course_id: int, top_k: int = 5) -> list[int]:
        results = self.collection.query(
            query_texts=[query],
            n_results=top_k,
            where={"course_id": course_id}
        )
        
        if not results["ids"] or not results["ids"][0]:
            return []
            
        return [int(id_str) for id_str in results["ids"][0]]

    def delete_course(self, course_id: int) -> None:
        self.collection.delete(
            where={"course_id": course_id}
        )


class PgVectorStore(VectorStore):
    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        # Use sentence-transformers for local embeddings (384 dimensions)
        self.model = SentenceTransformer('all-MiniLM-L6-v2')
        self._init_db()

    def _get_conn(self):
        conn = psycopg2.connect(self.connection_string)
        register_vector(conn)
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS chunk_embeddings (
                        id SERIAL PRIMARY KEY,
                        chunk_id INTEGER NOT NULL,
                        course_id INTEGER NOT NULL,
                        embedding vector(384)
                    );
                """)
                # Optional: Index for faster similarity search
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_course_id ON chunk_embeddings(course_id);")
            conn.commit()

    def add_chunks(self, chunks: Sequence[DocumentChunk]) -> None:
        if not chunks:
            return
            
        texts = [chunk.text for chunk in chunks]
        embeddings = self.model.encode(texts)
        
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                for chunk, embedding in zip(chunks, embeddings):
                    emb_list = embedding.tolist()
                    cur.execute(
                        "INSERT INTO chunk_embeddings (chunk_id, course_id, embedding) VALUES (%s, %s, %s)",
                        (chunk.id, chunk.course_id, emb_list)
                    )
            conn.commit()

    def search(self, query: str, course_id: int, top_k: int = 5) -> list[int]:
        query_embedding = self.model.encode(query).tolist()
        
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                # Use L2 distance (<->) for similarity search
                cur.execute("""
                    SELECT chunk_id 
                    FROM chunk_embeddings 
                    WHERE course_id = %s 
                    ORDER BY embedding <-> %s::vector 
                    LIMIT %s
                """, (course_id, query_embedding, top_k))
                results = cur.fetchall()
                
        return [row[0] for row in results]

    def delete_course(self, course_id: int) -> None:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chunk_embeddings WHERE course_id = %s", (course_id,))
            conn.commit()


def get_vector_store() -> VectorStore:
    """Factory to return the appropriate VectorStore based on configuration."""
    if settings.is_self_hosted:
        return ChromaVectorStore(persist_directory=settings.chroma_persist_directory)
    elif settings.is_hosted:
        return PgVectorStore(connection_string=settings.database_url)
    else:
        raise ValueError(f"Unknown deployment mode: {settings.deployment_mode}")
