from typing import List, Dict, Any, Optional
import logging
from datetime import datetime
import uuid
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document
from langchain_openai import OpenAIEmbeddings
from qdrant_client import models
import asyncio
import PyPDF2
from io import BytesIO

logger = logging.getLogger(__name__)

class DocumentEmbeddingService:
    def __init__(self, qdrant_service):
        """Initialize Document Embedding Service"""
        self.qdrant_service = qdrant_service
        self.embeddings = OpenAIEmbeddings(
            model="text-embedding-3-small"
        )
        
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,  # Optimized chunk size for precise retrieval
            chunk_overlap=50,
            separators=["\n\n", "\n", ". ", " ", ""],
            length_function=len,
            keep_separator=True
        )
    
    def extract_text_from_pdf(self, pdf_bytes: bytes) -> str:
        """Extract text from a PDF using PyPDF2."""
        try:
            reader = PyPDF2.PdfReader(BytesIO(pdf_bytes))
            text = ""
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
            return text
        except Exception as e:
            logger.error(f"Error extracting text from PDF: {str(e)}")
            return ""
    
    def extract_text_from_document(self, content: Any, file_type: str) -> str:
        """Extract text from different document types"""
        try:
            if isinstance(content, bytes):
                if file_type == "application/pdf":
                    return self.extract_text_from_pdf(content)
                # Add handling for DOCX if needed
                # elif file_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                #     return self.extract_text_from_docx(content)
                else:
                    # Default to UTF-8 text
                    return content.decode('utf-8', errors='ignore')
            else:
                # If it's already a string
                return content
        except Exception as e:
            logger.error(f"Error extracting text from document: {str(e)}")
            return ""
    
    async def process_documents(self, documents: List[Dict[str, Any]]) -> List[Document]:
        """Process documents into chunks with metadata"""
        processed_docs = []
        for doc in documents:
            content = doc['content']
            file_type = doc['metadata'].get('file_type', 'text/plain')
            
            # Extract text based on file type
            text = self.extract_text_from_document(content, file_type)
            logger.info(f"Extracted text from {file_type} document (first 200 chars): {text[:200]}")
            
            if not text:
                logger.warning(f"No text extracted from document {doc.get('id')}")
                continue
            
            # Split the extracted text into chunks
            chunks = self.text_splitter.split_text(text)
            for i, chunk in enumerate(chunks):
                metadata = {
                    **doc.get('metadata', {}),
                    'chunk_id': i,
                    'total_chunks': len(chunks),
                    'doc_id': doc.get('id'),
                    'processed_at': datetime.utcnow().isoformat()
                }
                processed_docs.append(Document(page_content=chunk, metadata=metadata))
        
        return processed_docs
    
    async def embed_documents(
        self, 
        company_id: str, 
        agent_id: str,
        documents: List[Dict[str, Any]]
    ) -> bool:
        """Embed documents and store in vector database"""
        try:
            logger.info(f"Embedding {len(documents)} documents for agent {agent_id}")
            
            # Process documents into chunks
            processed_docs = await self.process_documents(documents)
            
            if not processed_docs:
                logger.warning("No documents processed successfully")
                return True
            
            # Create points for Qdrant
            points = []
            for doc in processed_docs:
                # Generate embedding
                embedding = await self.embeddings.aembed_query(doc.page_content)
                
                # Create point
                points.append(models.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=embedding,
                    payload={
                        'page_content': doc.page_content,
                        'metadata': doc.metadata,
                        'type': 'document'
                    }
                ))
            
            # Add points to Qdrant
            result = await self.qdrant_service.add_points(company_id, points)
            
            logger.info(f"Embedded {len(points)} document chunks for agent {agent_id}")
            return result
            
        except Exception as e:
            logger.error(f"Error embedding documents: {str(e)}")
            return False