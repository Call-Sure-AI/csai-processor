from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from typing import List, Optional
from sqlalchemy.orm import Session
import logging
from datetime import datetime
import uuid
from database.config import get_db
from database.models import Company, Agent, Document, DocumentType
from services.pdf_processor_service import PDFProcessorService
from services.vector_store.qdrant_service import QdrantService
from handlers.s3_handler import S3Handler, S3Config

router = APIRouter()
logger = logging.getLogger(__name__)

qdrant_service = QdrantService()
s3_handler = S3Handler(S3Config())
pdf_processor = PDFProcessorService(qdrant_service, s3_handler)


@router.post("/upload-pdfs")
async def upload_pdfs(
    files: List[UploadFile] = File(...),
    company_id: str = Form(...),
    agent_id: str = Form(...),
    document_type: str = Form("custom"),
    db: Session = Depends(get_db)
):
    """
    Upload multiple PDF files and process them for RAG
    
    - files: List of PDF files to upload
    - company_id: Company ID
    - agent_id: Agent ID to associate documents with
    - document_type**: Type of document (faq, product, policy, technical, custom)
    """
    try:
        # Validate company and agent exist
        company = db.query(Company).filter(Company.id == company_id).first()
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")
        
        agent = db.query(Agent).filter(Agent.id == agent_id, Agent.company_id == company_id).first()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        # Validate file types
        invalid_files = [f.filename for f in files if not f.filename.endswith('.pdf')]
        if invalid_files:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file types. Only PDF files are allowed: {invalid_files}"
            )
        
        logger.info(f"Processing {len(files)} PDF files for company {company_id}, agent {agent_id}")
        
        # Process PDFs
        results = await pdf_processor.process_multiple_pdfs(
            files=files,
            company_id=company_id,
            agent_id=agent_id,
            document_type=document_type
        )
        
        # Save document records to database
        successful_docs = []
        for result in results:
            if result['success']:
                try:
                    doc = Document(
                        company_id=company_id,
                        agent_id=agent_id,
                        name=result['filename'],
                        type=DocumentType[document_type],
                        content=f"PDF document with {result['chunks_created']} chunks",
                        file_type="pdf",
                        file_size=result['file_size'],
                        original_filename=result['filename'],
                        chunk_count=result['chunks_created'],
                        embedding_id=result['document_id'],
                        last_embedded=datetime.utcnow()
                    )
                    db.add(doc)
                    successful_docs.append(result)
                except Exception as e:
                    logger.error(f"Error saving document record: {str(e)}")
        
        db.commit()
        
        # Calculate summary
        successful_count = len([r for r in results if r['success']])
        failed_count = len(results) - successful_count
        
        return {
            "success": True,
            "message": f"Processed {successful_count} files successfully, {failed_count} failed",
            "total_files": len(files),
            "successful": successful_count,
            "failed": failed_count,
            "results": results
        }
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error uploading PDFs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/documents/{company_id}")
async def get_documents(
    company_id: str,
    agent_id: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get list of documents for a company/agent"""
    try:
        query = db.query(Document).filter(Document.company_id == company_id)
        
        if agent_id:
            query = query.filter(Document.agent_id == agent_id)
        
        documents = query.all()
        
        return {
            "success": True,
            "count": len(documents),
            "documents": [
                {
                    "id": doc.id,
                    "name": doc.name,
                    "type": doc.type.value,
                    "file_size": doc.file_size,
                    "chunk_count": doc.chunk_count,
                    "created_at": doc.created_at.isoformat()
                }
                for doc in documents
            ]
        }
        
    except Exception as e:
        logger.error(f"Error getting documents: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    db: Session = Depends(get_db)
):
    """Delete a document and its embeddings"""
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        
        if document.embedding_id:
            try:
                await s3_handler.delete_file(document.embedding_id)
            except Exception as e:
                logger.warning(f"Could not delete from S3: {str(e)}")
        
        db.delete(document)
        db.commit()
        
        return {
            "success": True,
            "message": f"Document {document_id} deleted successfully"
        }
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error deleting document: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
