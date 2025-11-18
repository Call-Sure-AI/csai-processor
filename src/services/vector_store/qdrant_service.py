from typing import Dict, List, Optional, Any
import logging
from qdrant_client import QdrantClient, models
from langchain_openai import OpenAIEmbeddings
from config.settings import settings
import asyncio
from datetime import datetime

logger = logging.getLogger(__name__)

class QdrantService:
    def __init__(self):
        """Initialize Qdrant service with dedicated collection for this application"""
        self.embeddings = OpenAIEmbeddings(
            model="text-embedding-3-small",
            openai_api_key=settings.openai_api_key,
            client=None
        )
        qdrant_host = getattr(settings, 'QDRANT_HOST', 'localhost')
        qdrant_port = getattr(settings, 'QDRANT_PORT', 6333)
        qdrant_url = f"http://{qdrant_host}:{qdrant_port}"

        self.qdrant_client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            timeout=30
        )
        
        # Dedicated collection name for this application (separate from other apps)
        self.collection_name = settings.qdrant_collection_name or "voice_agent_documents"
        
    async def initialize_collection(self) -> bool:
        """Initialize the dedicated collection for this application"""
        try:
            # Check if collection exists
            collections = self.qdrant_client.get_collections()
            collection_exists = any(
                col.name == self.collection_name 
                for col in collections.collections
            )
            
            if not collection_exists:
                logger.info(f"Creating new collection: {self.collection_name}")
                
                # Create collection with proper vector configuration
                self.qdrant_client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=1536,  # text-embedding-3-small dimension
                        distance=models.Distance.COSINE
                    ),
                    # Optimize for multi-tenancy within this collection
                    hnsw_config=models.HnswConfigDiff(
                        payload_m=16,
                        m=0
                    )
                )
                
                # Create indexes for better search performance
                # Company ID index
                self.qdrant_client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="company_id",
                    field_schema=models.PayloadSchemaType.KEYWORD,
                    field_index_params=models.KeywordIndexParams(
                        is_tenant=True  # Optimizes for multi-tenancy
                    )
                )
                
                # Agent ID index
                self.qdrant_client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="agent_id",
                    field_schema=models.PayloadSchemaType.KEYWORD
                )
                
                # Document type index
                self.qdrant_client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="document_type",
                    field_schema=models.PayloadSchemaType.KEYWORD
                )
                
                # Document ID index
                self.qdrant_client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="document_id",
                    field_schema=models.PayloadSchemaType.KEYWORD
                )
                
                logger.info(f"Created collection: {self.collection_name} with indexes")
            else:
                logger.info(f"Using existing collection: {self.collection_name}")
            
            # Verify collection
            collection_info = self.qdrant_client.get_collection(self.collection_name)
            logger.info(f"Collection info: {collection_info.vectors_count} vectors, {collection_info.indexed_vectors_count} indexed")
            
            return True
            
        except Exception as e:
            logger.error(f"Error initializing collection: {str(e)}")
            return False
    
    async def add_points(
        self,
        company_id: str,
        points: List[models.PointStruct],
        agent_id: Optional[str] = None
    ) -> bool:
        """Add points to the dedicated collection"""
        try:
            # Ensure each point has required metadata for filtering
            for point in points:
                # Flatten metadata to top level for indexing
                if "metadata" in point.payload:
                    metadata = point.payload["metadata"]
                    point.payload["company_id"] = metadata.get("company_id", company_id)
                    point.payload["agent_id"] = metadata.get("agent_id", agent_id)
                    point.payload["document_type"] = metadata.get("document_type", "custom")
                    point.payload["document_id"] = metadata.get("document_id", "")
                    point.payload["document_name"] = metadata.get("document_name", "")
                else:
                    point.payload["company_id"] = company_id
                    point.payload["agent_id"] = agent_id
                    point.payload["document_type"] = "custom"
            
            self.qdrant_client.upsert(
                collection_name=self.collection_name,
                points=points,
                wait=True
            )
            
            logger.info(f"Added {len(points)} points for company {company_id}, agent {agent_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error adding points: {str(e)}")
            return False

    async def search(
        self,
        company_id: str,
        query_vector: List[float],
        agent_id: Optional[str] = None,
        document_type: Optional[str] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Search with filtering"""
        try:
            must_conditions = [
                models.FieldCondition(
                    key="company_id",
                    match=models.MatchValue(value=company_id)
                )
            ]
            
            if agent_id:
                must_conditions.append(
                    models.FieldCondition(
                        key="agent_id",
                        match=models.MatchValue(value=agent_id)
                    )
                )

            results = self.qdrant_client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                query_filter=models.Filter(must=must_conditions),
                limit=limit,
                with_payload=True,
                score_threshold=0.3
            )
            
            logger.info(f"🔍 Found {len(results)} results for company {company_id}, agent {agent_id}")

            if results:
                top = results[0]
                logger.info(f"Top result: score={top.score:.3f}, doc={top.payload.get('document_name', 'Unknown')}")
                logger.info(f"Content preview: {top.payload.get('page_content', '')[:100]}...")
            
            return [
                {
                    "id": result.id,
                    "score": result.score,
                    "content": result.payload.get("page_content", ""),
                    "document_name": result.payload.get("document_name", ""),
                    "metadata": result.payload.get("metadata", {})
                }
                for result in results
            ]
            
        except Exception as e:
            logger.error(f"Error searching: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return []

        
        async def delete_document(
            self, 
            company_id: str, 
            document_id: str,
            agent_id: Optional[str] = None
        ) -> bool:
            """Delete all chunks for a specific document"""
            try:
                filter_conditions = [
                    models.FieldCondition(
                        key="company_id",
                        match=models.MatchValue(value=company_id)
                    ),
                    models.FieldCondition(
                        key="document_id",
                        match=models.MatchValue(value=document_id)
                    )
                ]
                
                if agent_id:
                    filter_conditions.append(
                        models.FieldCondition(
                            key="agent_id",
                            match=models.MatchValue(value=agent_id)
                        )
                    )
                
                self.qdrant_client.delete(
                    collection_name=self.collection_name,
                    points_selector=models.FilterSelector(
                        filter=models.Filter(must=filter_conditions)
                    )
                )
                
                logger.info(f"Deleted document {document_id} for company {company_id}")
                return True
                
            except Exception as e:
                logger.error(f"Error deleting document: {str(e)}")
                return False
    
    async def delete_agent_data(self, company_id: str, agent_id: str) -> bool:
        """Delete all data for a specific agent"""
        try:
            self.qdrant_client.delete(
                collection_name=self.collection_name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="company_id",
                                match=models.MatchValue(value=company_id)
                            ),
                            models.FieldCondition(
                                key="agent_id",
                                match=models.MatchValue(value=agent_id)
                            )
                        ]
                    )
                )
            )
            
            logger.info(f"Deleted all data for company {company_id}, agent {agent_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting agent data: {str(e)}")
            return False
    
    async def delete_company_data(self, company_id: str) -> bool:
        """Delete all data for a company"""
        try:
            self.qdrant_client.delete(
                collection_name=self.collection_name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="company_id",
                                match=models.MatchValue(value=company_id)
                            )
                        ]
                    )
                )
            )
            
            logger.info(f"Deleted all data for company {company_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting company data: {str(e)}")
            return False
    
    async def get_stats(
        self, 
        company_id: Optional[str] = None, 
        agent_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get statistics for the collection or specific company/agent"""
        try:
            # Get overall collection stats
            collection_info = self.qdrant_client.get_collection(self.collection_name)
            
            stats = {
                "collection_name": self.collection_name,
                "total_vectors": collection_info.vectors_count,
                "indexed_vectors": collection_info.indexed_vectors_count,
                "status": collection_info.status
            }
            
            # If specific company/agent requested, count their vectors
            if company_id:
                filter_conditions = [
                    models.FieldCondition(
                        key="company_id",
                        match=models.MatchValue(value=company_id)
                    )
                ]
                
                if agent_id:
                    filter_conditions.append(
                        models.FieldCondition(
                            key="agent_id",
                            match=models.MatchValue(value=agent_id)
                        )
                    )
                
                # Count points using scroll
                result = self.qdrant_client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=models.Filter(must=filter_conditions),
                    limit=10000,
                    with_payload=False,
                    with_vectors=False
                )
                
                stats["company_id"] = company_id
                stats["agent_id"] = agent_id
                stats["company_vectors"] = len(result[0])
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting stats: {str(e)}")
            return {}
    
    async def list_documents(
        self, 
        company_id: str, 
        agent_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List all unique documents for a company/agent"""
        try:
            filter_conditions = [
                models.FieldCondition(
                    key="company_id",
                    match=models.MatchValue(value=company_id)
                )
            ]
            
            if agent_id:
                filter_conditions.append(
                    models.FieldCondition(
                        key="agent_id",
                        match=models.MatchValue(value=agent_id)
                    )
                )
            
            # Scroll through all points to get unique documents
            result = self.qdrant_client.scroll(
                collection_name=self.collection_name,
                scroll_filter=models.Filter(must=filter_conditions),
                limit=10000,
                with_payload=True,
                with_vectors=False
            )
            
            # Extract unique documents
            documents = {}
            for point in result[0]:
                doc_id = point.payload.get("document_id")
                if doc_id and doc_id not in documents:
                    documents[doc_id] = {
                        "document_id": doc_id,
                        "document_name": point.payload.get("document_name", ""),
                        "document_type": point.payload.get("document_type", ""),
                        "chunk_count": 0
                    }
                if doc_id:
                    documents[doc_id]["chunk_count"] += 1
            
            return list(documents.values())
            
        except Exception as e:
            logger.error(f"Error listing documents: {str(e)}")
            return []


# Global instance
qdrant_service = QdrantService()
