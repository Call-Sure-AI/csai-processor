"""
Connection Manager Implementation for WebSocket handling
"""
from typing import Dict, Optional, List, Any
import logging
import asyncio
import time
import json
from datetime import datetime
from fastapi import WebSocket
from contextlib import asynccontextmanager
from sqlalchemy.orm import Session
from uuid import UUID

from database.models import Company, Conversation, Agent, Document
from core.interfaces import (
    IConnectionManager, 
    IAgentManager, 
    IVectorStore, 
    IRAGService, 
    ILogger,
    IMessageProcessor
)
from config.settings import settings


class UUIDEncoder(json.JSONEncoder):
    """Custom JSON encoder for UUID and datetime objects"""
    def default(self, obj):
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class ConnectionManager(IConnectionManager):
    """
    Manages WebSocket connections with proper separation of concerns and thread safety
    """
    
    def __init__(
        self,
        db_session: Session,
        agent_manager: IAgentManager,
        vector_store: IVectorStore,
        rag_service: IRAGService,
        message_processor: IMessageProcessor,
        logger: Optional[ILogger] = None
    ):
        self.db = db_session
        self.agent_manager = agent_manager
        self.vector_store = vector_store
        self.rag_service = rag_service
        self.message_processor = message_processor
        self.logger = logger or logging.getLogger(__name__)
        
        # WebSocket management
        self.active_connections: Dict[str, WebSocket] = {}
        self.client_companies: Dict[str, Company] = {}
        self.client_conversations: Dict[str, Conversation] = {}
        
        # Connection monitoring
        self.connection_times: Dict[str, datetime] = {}
        self.message_counts: Dict[str, int] = {}
        self.active_agents: Dict[str, str] = {}  # client_id -> agent_id
        
        # Response caching
        self._response_cache = {}
        self._cache_size = 1000
        self._cache_ttl = 300  # 5 minutes
        
        # Request deduplication
        self._recent_requests = {}
        self._request_ttl = 3  # 3 seconds
        
        # Agent resources
        self.agent_resources = {}
        
        # JSON encoder
        self.json_encoder = UUIDEncoder()
        
        # Concurrent processing limits
        self._processing_semaphore = asyncio.Semaphore(10)
        
        # Request queue for batch processing
        self._request_queue = asyncio.Queue()
        self._batch_size = 5
        self._batch_timeout = 0.1  # 100ms
        
        # Connection states
        self._connection_states = {}
        self._state_lock = asyncio.Lock()
        
        # Thread safety
        self._connections_lock = asyncio.Lock()
        self._resources_lock = asyncio.Lock()
        
        # Start batch processor
        asyncio.create_task(self._process_batches())

    async def send_json(self, websocket: WebSocket, data: dict) -> bool:
        """Send JSON data with improved error handling and connection validation"""
        try:
            # First check if websocket is None
            if websocket is None:
                self.logger.warning("Attempted to send to null websocket")
                return False
                
            # Check if websocket is closed
            if self.websocket_is_closed(websocket):
                self.logger.warning("Websocket detected as closed")
                return False
                
            # Convert to JSON string with UUID handling
            json_str = json.dumps(data, cls=UUIDEncoder)
            
            # Send with timeout to prevent hanging
            await asyncio.wait_for(
                websocket.send_text(json_str),
                timeout=5.0  # 5 second timeout
            )
            
            # Log success for important message types
            if data.get('type') in ['config', 'connection_ack', 'stream_chunk']:
                msg_type = data.get('type')
                if msg_type == 'stream_chunk':
                    # Only log the first chunk to avoid log spam
                    if data.get('chunk_number', 0) == 1:
                        self.logger.info(f"Sending first stream chunk for message {data.get('msg_id', 'unknown')}")
                else:
                    self.logger.info(f"Successfully sent {msg_type} message")
                
            return True
            
        except asyncio.TimeoutError:
            self.logger.error(f"Timeout sending {data.get('type', 'unknown')} message")
            return False
        except Exception as e:
            if "disconnected" in str(e).lower() or "closed" in str(e).lower():
                self.logger.warning(f"Client disconnected during send operation: {str(e)}")
            else:
                self.logger.error(f"Error sending JSON: {str(e)}", exc_info=True)
            return False
        
    async def _process_batches(self):
        """Process message batches for better performance"""
        while True:
            batch = []
            try:
                request = await self._request_queue.get()
                batch.append(request)
                
                timeout = self._batch_timeout
                while len(batch) < self._batch_size:
                    try:
                        request = await asyncio.wait_for(
                            self._request_queue.get(),
                            timeout=timeout
                        )
                        batch.append(request)
                    except asyncio.TimeoutError:
                        break
                
                responses = await asyncio.gather(
                    *[self._process_single_request(req) for req in batch],
                    return_exceptions=True
                )
                
                for req, res in zip(batch, responses):
                    if not isinstance(res, Exception):
                        await self._send_response(
                            req['client_id'], 
                            res, 
                            req.get('metadata', {})
                        )
                    
            except Exception as e:
                self.logger.error(f"Batch processing error: {str(e)}")

    async def _process_single_request(self, request: Dict) -> Dict:
        """Process a single request"""
        try:
            client_id = request['client_id']
            message_data = request['data']
            
            # Process message using message processor
            response = await self.message_processor.process_message(
                client_id=client_id,
                message_data=message_data,
                conversation_manager=self.conversation_manager,
                agent_manager=self.agent_manager,
                rag_service=self.rag_service
            )
            
            return {
                'type': 'response',
                'content': response,
                'client_id': client_id
            }
            
        except Exception as e:
            self.logger.error(f"Error processing single request: {str(e)}")
            return {
                'type': 'error',
                'error': str(e),
                'client_id': request.get('client_id', 'unknown')
            }

    async def initialize_agent_resources(self, client_id: str, company_id: str, agent_info: dict):
        """Initialize agent resources with proper embedding handling"""
        try:
            async with self._resources_lock:
                # Get the agent from the database
                agent_id = agent_info.get('id')
                if not agent_id:
                    self.logger.error(f"No agent_id provided for {client_id}")
                    return False
                    
                # Fetch the agent record to get additional_context
                agent_record = self.db.query(Agent).filter_by(id=agent_id).first()
                if not agent_record:
                    self.logger.error(f"Agent {agent_id} not found")
                    return False
                    
                # Extract businessContext and roleDescription from additional_context
                additional_context = agent_record.additional_context or {}
                business_context = additional_context.get('businessContext', '')
                role_description = additional_context.get('roleDescription', '')
                
                # Create prompt based on additional_context
                prompt = agent_record.prompt
                if business_context and role_description:
                    prompt = f"{business_context} {role_description}"
                elif business_context:
                    prompt = business_context
                elif role_description:
                    prompt = role_description
                    
                # Update agent_info with the new prompt
                agent_info = {
                    "id": agent_id,
                    "name": agent_record.name,
                    "type": agent_record.type,
                    "prompt": prompt,
                    "confidence_threshold": agent_record.confidence_threshold,
                    "additional_context": agent_record.additional_context
                }
                
                # Create RAG service instance
                rag_service = self.rag_service
                
                # Create chain with existing embeddings and the updated prompt
                chain = await rag_service.create_qa_chain(
                    company_id=company_id,
                    agent_id=agent_info['id'],
                    agent_prompt=prompt
                )
                
                self.agent_resources[client_id] = {
                    "rag_service": rag_service,
                    "chain": chain,
                    "agent_info": agent_info
                }
                
                self.logger.info(f"Successfully initialized agent resources for {agent_info['id']}")
                return True

        except Exception as e:
            self.logger.error(f"Error initializing agent resources: {str(e)}")
            self.db.rollback()
            return False
        
    async def load_agent_documents(self, company_id: str, agent_id: str) -> List[Dict]:
        """Load agent's documents from database"""
        try:
            documents = self.db.query(Document).filter_by(
                agent_id=agent_id,
                company_id=company_id
            ).all()
            
            return [{
                'id': doc.id,
                'content': doc.content,
                'metadata': {
                    'agent_id': doc.agent_id,
                    'file_type': doc.file_type,
                    'original_filename': doc.original_filename,
                    'doc_type': doc.type
                }
            } for doc in documents]

        except Exception as e:
            self.logger.error(f"Error loading agent documents: {str(e)}")
            return []
    
    async def cleanup_agent_resources(self, client_id: str):
        """Clean up resources with state tracking"""
        try:
            async with self._resources_lock:
                if client_id in self.agent_resources:
                    self.agent_resources.pop(client_id)
                    
                if client_id in self._connection_states:
                    self._connection_states[client_id]["initialized"] = False
                    
                self.logger.info(f"Cleaned up agent resources for client {client_id}")
                
        except Exception as e:
            self.logger.error(f"Error cleaning up agent resources: {str(e)}")
    
    async def initialize_client(self, client_id: str) -> None:
        """Initialize client with proper error handling"""
        try:
            company_info = self.client_companies.get(client_id)
            if not company_info:
                return

            if not self.agent_manager:
                raise ValueError("Agent manager not initialized")
            
            await self.agent_manager.initialize_company_agents(company_info['id'])
            
            # Send available agents list
            websocket = self.active_connections.get(client_id)
            if websocket and not self.websocket_is_closed(websocket):
                agents = await self.agent_manager.get_company_agents(company_info['id'])
                data = {
                    "type": "agents",
                    "data": agents
                }
                await self.send_json(websocket, data)

        except Exception as e:
            self.logger.error(f"Error initializing client: {str(e)}")
            await self.handle_error(client_id, str(e))
    
    async def connect(self, websocket: WebSocket, client_id: str) -> None:
        """Initialize connection with proper state tracking"""
        async with self._state_lock:
            try:
                # Add connection tracking
                self._connection_states[client_id] = {
                    "connected": True,
                    "initialized": False,
                    "last_activity": datetime.utcnow()
                }
                
                async with self._connections_lock:
                    self.active_connections[client_id] = websocket
                    self.message_counts[client_id] = 0
                    self.connection_times[client_id] = datetime.utcnow()
                
                self.logger.info(f"Client {client_id} connected")
                
            except Exception as e:
                self.logger.error(f"Connection error: {str(e)}")
                self._connection_states[client_id] = {"connected": False}
                raise
    
    def disconnect(self, client_id: str) -> None:
        """Handle disconnection with proper state cleanup"""
        try:
            websocket = self.active_connections.get(client_id)
            if websocket and not self.websocket_is_closed(websocket):
                asyncio.create_task(websocket.close())
            
            # Update connection state
            if client_id in self._connection_states:
                self._connection_states[client_id]["connected"] = False
            
            # Remove from active connections
            self.active_connections.pop(client_id, None)
            self.client_companies.pop(client_id, None)
            self.client_conversations.pop(client_id, None)
            self.message_counts.pop(client_id, None)
            self.active_agents.pop(client_id, None)
            self.connection_times.pop(client_id, None)
            
            # Clean up resources
            asyncio.create_task(self.cleanup_agent_resources(client_id))
            
            self.logger.info(f"Client {client_id} disconnected")
            
        except Exception as e:
            self.logger.error(f"Error in disconnect: {str(e)}")
    
    async def close_all_connections(self):
        """Close all connections gracefully"""
        try:
            close_tasks = []
            async with self._connections_lock:
                for client_id, websocket in self.active_connections.items():
                    if not websocket.closed:
                        try:
                            await websocket.send_json({
                                "type": "system",
                                "message": "Server shutting down"
                            })
                            close_tasks.append(websocket.close())
                        except Exception as e:
                            self.logger.error(f"Error closing connection {client_id}: {str(e)}")
            
            if close_tasks:
                await asyncio.gather(*close_tasks, return_exceptions=True)
                
            async with self._connections_lock:
                self.active_connections.clear()
                self.client_companies.clear()
                self.client_conversations.clear()
            
        except Exception as e:
            self.logger.error(f"Error closing connections: {str(e)}")

    async def process_message(self, client_id: str, message_data: dict) -> None:
        """Process incoming message with proper error handling"""
        try:
            # Add to request queue for batch processing
            await self._request_queue.put({
                'client_id': client_id,
                'data': message_data,
                'metadata': {
                    'timestamp': datetime.utcnow().isoformat(),
                    'message_type': message_data.get('type', 'unknown')
                }
            })
            
        except Exception as e:
            self.logger.error(f"Error processing message: {str(e)}")
            await self.handle_error(client_id, str(e))

    async def send_welcome_message(self, client_id: str):
        """Send welcome message to client"""
        try:
            websocket = self.active_connections.get(client_id)
            company_info = self.client_companies.get(client_id)
            if not websocket or websocket.closed or not company_info:
                return

            welcome_msg = f"Welcome to {company_info['name']}!"
            agent_id = None

            if self.agent_manager:
                base_agent = await self.agent_manager.get_base_agent(company_info['id'])
                if base_agent:
                    self.active_agents[client_id] = base_agent['id']
                    agent_id = base_agent['id']

            await websocket.send_text(self.json_encoder.encode({
                "type": "system",
                "message": welcome_msg,
                "metadata": {
                    "company_name": company_info['name'],
                    "agent_id": agent_id
                }
            }))

        except Exception as e:
            self.logger.error(f"Error sending welcome: {str(e)}")
            await self.handle_error(client_id, str(e))
    
    async def handle_error(self, client_id: str, error_message: str):
        """Handle error with proper logging"""
        try:
            websocket = self.active_connections.get(client_id)
            if websocket and not self.websocket_is_closed(websocket):
                await websocket.send_json({
                    "type": "error",
                    "error": {
                        "message": error_message,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                })
        except Exception as e:
            self.logger.error(f"Error handling error: {str(e)}")
    
    async def handle_connection_error(self, websocket: WebSocket, client_id: str):
        """Handle connection error"""
        try:
            if not websocket._client_state.closed:
                await websocket.close(code=1011)
            self.disconnect(client_id)
        except Exception as e:
            self.logger.error(f"Error handling connection error: {str(e)}")

    @staticmethod
    def websocket_is_closed(websocket: WebSocket) -> bool:
        """Check if websocket is closed with better error handling"""
        try:
            # Check application and client state if available
            app_state = getattr(websocket, 'application_state', None)
            client_state = getattr(websocket, 'client_state', None)
            
            # Check explicit closed attribute
            explicitly_closed = getattr(websocket, '_closed', False)
            
            # For FastAPI WebSockets
            if app_state and client_state:
                return (app_state.name == "DISCONNECTED" or 
                        client_state.name == "DISCONNECTED" or
                        explicitly_closed)
            
            # For other WebSocket implementations
            return explicitly_closed
        except AttributeError:
            # Only return True for AttributeError on specific checks
            return False
        except Exception as e:
            # Log other exceptions but don't assume socket is closed
            logging.error(f"Error checking websocket state: {str(e)}")
            return False

    async def _send_response(self, client_id: str, response: Dict, metadata: Dict):
        """Send response to client"""
        try:
            websocket = self.active_connections.get(client_id)
            if websocket and not self.websocket_is_closed(websocket):
                await self.send_json(websocket, response)
        except Exception as e:
            self.logger.error(f"Error sending response to {client_id}: {str(e)}")

    def get_connection_stats(self) -> Dict[str, Any]:
        """Get connection statistics"""
        try:
            return {
                "active_connections": len(self.active_connections),
                "total_messages": sum(self.message_counts.values()),
                "connection_times": {
                    client_id: (datetime.utcnow() - time).total_seconds()
                    for client_id, time in self.connection_times.items()
                },
                "agent_resources": len(self.agent_resources),
                "cache_size": len(self._response_cache)
            }
        except Exception as e:
            self.logger.error(f"Error getting connection stats: {str(e)}")
            return {}
