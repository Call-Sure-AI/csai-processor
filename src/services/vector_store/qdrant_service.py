from typing import Dict, List, Optional, Any
import logging
from qdrant_client import QdrantClient, models
from langchain_openai import OpenAIEmbeddings
from langchain_qdrant import QdrantVectorStore
from config.settings import settings
import asyncio
from datetime import datetime

logger = logging.getLogger(__name__)

class QdrantService:
    def __init__(self):
        """Initialize Qdrant service with necessary components"""
        self.embeddings = OpenAIEmbeddings(
            model="text-embedding-3-small",
            openai_api_key=settings.openai_api_key,
            client=None
        )
        
        self.qdrant_client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            timeout=30  # Added timeout for better reliability
        )
        
        # Cache for vector stores
        self.vector_stores = {}
    
    async def setup_collection(self, company_id: str) -> bool:
        """Setup or verify company collection exists"""
        try:
            collection_name = f"company_{company_id}"
            
            # Check if collection exists
            collections = self.qdrant_client.get_collections()
            collection_exists = any(col.name == collection_name for col in collections.collections)
            
            if not collection_exists:
                # Create collection if it doesn't exist
                self.qdrant_client.create_collection(
                    collection_name=collection_name,
                    vectors_config=models.VectorParams(
                        size=1536,  # For text-embedding-3-small
                        distance=models.Distance.COSINE
                    )
                )
                
                # Create index for faster filtering
                self.qdrant_client.create_payload_index(
                    collection_name=collection_name,
                    field_name="metadata.agent_id",
                    field_schema="keyword"
                )
                
                logger.info(f"Created collection and index: {collection_name}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error setting up collection: {str(e)}")
            return False
    
    async def get_vector_store(self, company_id: str) -> QdrantVectorStore:
        """Get or create a vector store for a company"""
        try:
            collection_name = f"company_{company_id}"
            
            # Return cached vector store if available
            if collection_name in self.vector_stores:
                return self.vector_stores[collection_name]
            
            # Ensure collection exists
            await self.setup_collection(company_id)
            
            # Create vector store
            vector_store = QdrantVectorStore.from_existing_collection(
                embedding=self.embeddings,
                collection_name=collection_name,
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key,
                content_payload_key="page_content",
                metadata_payload_key="metadata"
            )
            # Cache the vector store
            self.vector_stores[collection_name] = vector_store
            return vector_store
            
        except Exception as e:
            logger.error(f"Error getting vector store: {str(e)}")
            raise
    
    async def add_points(
        self, 
        company_id: str, 
        points: List[models.PointStruct]
    ) -> bool:
        """Add points to Qdrant collection"""
        try:
            collection_name = f"company_{company_id}"
            
            # Ensure collection exists
            await self.setup_collection(company_id)
            
            # Add points in smaller batches to avoid 413 errors
            batch_size = 10  # Reduce from 100 to 10
            for i in range(0, len(points), batch_size):
                batch = points[i:i + batch_size]
                try:
                    self.qdrant_client.upsert(
                        collection_name=collection_name,
                        points=batch,
                        wait=True
                    )
                    logger.info(f"Added batch {i//batch_size + 1}/{(len(points)-1)//batch_size + 1} ({len(batch)} points)")
                except Exception as batch_e:
                    logger.error(f"Error adding batch {i//batch_size + 1}: {str(batch_e)}")
                    # Try with even smaller batches if possible
                    if len(batch) > 1:
                        for point in batch:
                            try:
                                self.qdrant_client.upsert(
                                    collection_name=collection_name,
                                    points=[point],
                                    wait=True
                                )
                                logger.info(f"Added single point after batch failure")
                            except Exception as point_e:
                                logger.error(f"Failed to add point: {str(point_e)}")
            
            logger.info(f"Added {len(points)} points to {collection_name}")
            return True
            
        except Exception as e:
            logger.error(f"Error adding points: {str(e)}")
            return False
    
    async def delete_agent_data(self, company_id: str, agent_id: str) -> bool:
        """Delete all data associated with an agent"""
        try:
            collection_name = f"company_{company_id}"
            
            # Delete points by filter
            self.qdrant_client.delete(
                collection_name=collection_name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="metadata.agent_id",
                                match=models.MatchValue(value=agent_id)
                            )
                        ]
                    )
                )
            )
            
            # Clear cache
            if collection_name in self.vector_stores:
                del self.vector_stores[collection_name]
            
            logger.info(f"Deleted data for agent {agent_id} in collection {collection_name}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting agent data: {str(e)}")
            return False
    
    async def get_existing_embeddings(
        self, 
        company_id: str, 
        agent_id: Optional[str] = None
    ) -> List[Dict]:
        """Get existing embeddings for a company/agent"""
        try:
            collection_name = f"company_{company_id}"
            
            # Prepare filter
            search_filter = None
            if agent_id:
                search_filter = models.Filter(
                    must=[
                        models.FieldCondition(
                            key="metadata.agent_id",
                            match=models.MatchValue(value=agent_id)
                        )
                    ]
                )
            
            # Get points
            response = self.qdrant_client.scroll(
                collection_name=collection_name,
                filter=search_filter,
                limit=100,
                with_payload=True,
                with_vectors=True
            )
            
            return [
                {
                    "id": point.id,
                    "vector": point.vector,
                    "payload": point.payload
                }
                for point in response[0]
            ]
            
        except Exception as e:
            logger.error(f"Error getting existing embeddings: {str(e)}")
            return []
    
    async def verify_embeddings(self, company_id: str, agent_id: Optional[str] = None) -> bool:
        """Verify that embeddings exist and are accessible"""
        try:
            logger.info(f"Verifying embeddings for company {company_id}, agent {agent_id}")
            
            embeddings = await self.get_existing_embeddings(company_id, agent_id)
            if not embeddings:
                logger.warning(f"No embeddings found for company {company_id} and agent {agent_id}")
                return False
            
            logger.info(f"Found {len(embeddings)} embeddings")
            
            # Verify a sample embedding
            sample = embeddings[0]
            if not sample.get('vector') or not sample.get('payload'):
                logger.error("Invalid embedding format found")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error verifying embeddings: {str(e)}")
            return False