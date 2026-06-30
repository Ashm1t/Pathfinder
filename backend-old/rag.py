import os
from typing import List, Dict, Any
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
try:
    from langchain_core.documents import Document
except ImportError:
    from langchain.schema import Document
import shutil

# Persistence directory for ChromaDB
CHROMA_PATH = "chroma_db"

class RAGEngine:
    def __init__(self):
        # Initialize embeddings (using a lightweight model)
        self.embedding_function = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
        
        # Initialize Vector DB
        self.db = Chroma(
            persist_directory=CHROMA_PATH, 
            embedding_function=self.embedding_function
        )

    def ingest_documents(self, documents: List[Dict[str, Any]]):
        """
        Ingests a list of documents (text content) into the vector database.
        documents format: [{"content": "text...", "metadata": {"source": "doc1.txt"}}]
        """
        docs = []
        for doc in documents:
            docs.append(Document(page_content=doc["content"], metadata=doc["metadata"]))
            
        # Split text into chunks
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
        chunks = text_splitter.split_documents(docs)
        
        # Add to Chroma
        if chunks:
            self.db.add_documents(chunks)
            # self.db.persist() # Chroma 0.4+ persists automatically

    def query(self, query_text: str, n_results: int = 3) -> List[Document]:
        """
        Search the knowledge base for relevant documents.
        """
        results = self.db.similarity_search(query_text, k=n_results)
        return results

    def clear_db(self):
        """
        Clears the vector database.
        """
        if os.path.exists(CHROMA_PATH):
            shutil.rmtree(CHROMA_PATH)
            # Re-initialize
            self.db = Chroma(
                persist_directory=CHROMA_PATH, 
                embedding_function=self.embedding_function
            )

# Singleton instance
rag_engine = RAGEngine()
