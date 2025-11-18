from typing import List, Dict, Any, Optional
import logging
import uuid
from datetime import datetime
from PyPDF2 import PdfReader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from qdrant_client import models
from fastapi import UploadFile
import asyncio

logger = logging.getLogger(__name__)

class PDFProcessorService:
    """Service for processing PDF files and creating embeddings"""
    
    def __init__(self, qdrant_service, s3_handler):
        self.qdrant_service = qdrant_service
        self.s3_handler = s3_handler
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            length_function=len,
            separators=["\n\n", "\n", " ", ""]
        )
    
    async def extract_text_from_pdf(self, file_content: bytes, filename: str) -> str:
        """Extract text from PDF file"""
        try:
            import io
            pdf_file = io.BytesIO(file_content)
            reader = PdfReader(pdf_file)
            
            text = ""
            for page_num, page in enumerate(reader.pages):
                page_text = page.extract_text()
                text += f"\n--- Page {page_num + 1} ---\n{page_text}"
            
            logger.info(f"Extracted {len(text)} characters from {filename}")
            return text
            
        except Exception as e:
            logger.error(f"Error extracting text from PDF {filename}: {str(e)}")
            raise
    
    async def process_pdf(
        self,
        file: UploadFile,
        company_id: str,
        agent_id: str,
        document_type: str = "custom"
    ) -> Dict[str, Any]:
        """Process a single PDF file and store in Qdrant"""
        try:
            file_content = await file.read()
            await file.seek(0)
            
            s3_result = await self.s3_handler.upload_file(
                file=file,
                enable_public_read_access=False,
                custom_key=f"documents/{company_id}/{agent_id}/{uuid.uuid4()}_{file.filename}"
            )
            
            if not s3_result['success']:
                raise Exception(f"S3 upload failed: {s3_result.get('error')}")
            
            text = await self.extract_text_from_pdf(file_content, file.filename)
            
            if not text or len(text.strip()) < 10:
                raise Exception(f"No text extracted from {file.filename}")
            
            chunks = self.text_splitter.split_text(text)
            logger.info(f"Split {file.filename} into {len(chunks)} chunks")
            
            embeddings = await self.qdrant_service.embeddings.aembed_documents(chunks)
            
            points = []
            for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                point_id = str(uuid.uuid4())
                points.append(
                    models.PointStruct(
                        id=point_id,
                        vector=embedding,
                        payload={
                            "page_content": chunk,
                            "metadata": {
                                "company_id": company_id,
                                "agent_id": agent_id,
                                "document_id": s3_result['key'],
                                "document_name": file.filename,
                                "document_type": document_type,
                                "chunk_index": idx,
                                "total_chunks": len(chunks),
                                "s3_url": s3_result['url'],
                                "created_at": datetime.utcnow().isoformat(),
                                "file_size": s3_result['data']['size']
                            }
                        }
                    )
                )
            
            success = await self.qdrant_service.add_points(company_id, points)
            
            if not success:
                raise Exception("Failed to add points to Qdrant")
            
            return {
                "success": True,
                "filename": file.filename,
                "document_id": s3_result['key'],
                "s3_url": s3_result['url'],
                "chunks_created": len(chunks),
                "file_size": s3_result['data']['size'],
                "message": f"Successfully processed {file.filename}"
            }
            
        except Exception as e:
            logger.error(f"Error processing PDF {file.filename}: {str(e)}")
            return {
                "success": False,
                "filename": file.filename,
                "error": str(e)
            }
    
    async def process_multiple_pdfs(
        self,
        files: List[UploadFile],
        company_id: str,
        agent_id: str,
        document_type: str = "custom"
    ) -> List[Dict[str, Any]]:
        """Process multiple PDF files in parallel"""
        try:
            tasks = [
                self.process_pdf(file, company_id, agent_id, document_type)
                for file in files
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            processed_results = []
            for result in results:
                if isinstance(result, Exception):
                    processed_results.append({
                        "success": False,
                        "error": str(result)
                    })
                else:
                    processed_results.append(result)
            
            return processed_results
            
        except Exception as e:
            logger.error(f"Error processing multiple PDFs: {str(e)}")
            raise
