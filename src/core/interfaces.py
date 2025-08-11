"""
Core interfaces for CSAI Processor following SOLID principles
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Protocol
from datetime import datetime
from sqlalchemy.orm import Session
from fastapi import WebSocket


class IConversationManager(ABC):
    """Interface for conversation management"""
    
    @abstractmethod
    async def get_or_create_conversation(
        self,
        customer_id: str,
        company_id: str,
        agent_id: Optional[str] = None
    ) -> tuple[Any, bool]:
        """Get existing conversation or create new one"""
        pass
    
    @abstractmethod
    async def create_conversation(
        self,
        customer_id: str,
        company_id: str,
        agent_id: Optional[str] = None
    ) -> Any:
        """Create a new conversation"""
        pass
    
    @abstractmethod
    async def update_conversation(
        self,
        conversation_id: str,
        user_message: str,
        ai_response: str,
        agent_id: str,
        metadata: Optional[Dict] = None
    ) -> None:
        """Update conversation with new messages"""
        pass
    
    @abstractmethod
    async def get_conversation_context(
        self,
        conversation_id: str,
        max_history: int = 5,
        include_system_prompt: bool = True
    ) -> List[Dict]:
        """Get conversation context"""
        pass
    
    @abstractmethod
    async def cleanup_cache(self, force: bool = False) -> None:
        """Clean up expired cache entries"""
        pass


class IAgentManager(ABC):
    """Interface for agent management"""
    
    @abstractmethod
    async def ensure_base_agent(self, company_id: str) -> Optional[Dict[str, Any]]:
        """Ensure base agent exists for company"""
        pass
    
    @abstractmethod
    async def initialize_company_agents(self, company_id: str) -> None:
        """Initialize agents for company"""
        pass
    
    @abstractmethod
    async def get_company_agents(self, company_id: str) -> List[Dict[str, Any]]:
        """Get all agents for company"""
        pass
    
    @abstractmethod
    async def get_base_agent(self, company_id: str) -> Optional[Dict[str, Any]]:
        """Get base agent for company"""
        pass
    
    @abstractmethod
    async def create_conversation(
        self,
        company_id: str,
        client_id: str
    ) -> Optional[Dict[str, Any]]:
        """Create conversation"""
        pass
    
    @abstractmethod
    async def find_best_agent(
        self,
        company_id: str,
        query: str,
        current_agent_id: Optional[str] = None
    ) -> tuple[Optional[str], float]:
        """Find best agent for query"""
        pass
    
    @abstractmethod
    async def update_conversation(
        self,
        conversation_id: str,
        user_message: str,
        ai_response: str,
        agent_id: str,
        confidence_score: float = 0.0,
        tokens_used: Optional[int] = None,
        was_successful: bool = True,
        previous_agent_id: Optional[str] = None
    ) -> bool:
        """Update conversation with interaction"""
        pass
    
    @abstractmethod
    async def get_conversation_context(
        self,
        conversation_id: str,
        limit: int = 5
    ) -> List[Dict[str, str]]:
        """Get conversation context"""
        pass


class IConnectionManager(ABC):
    """Interface for WebSocket connection management"""
    
    @abstractmethod
    async def connect(self, websocket: WebSocket, client_id: str) -> None:
        """Initialize connection"""
        pass
    
    @abstractmethod
    def disconnect(self, client_id: str) -> None:
        """Handle disconnection"""
        pass
    
    @abstractmethod
    async def send_json(self, websocket: WebSocket, data: dict) -> bool:
        """Send JSON data"""
        pass
    
    @abstractmethod
    async def process_message(self, client_id: str, message_data: dict) -> None:
        """Process incoming message"""
        pass
    
    @abstractmethod
    async def initialize_client(self, client_id: str) -> None:
        """Initialize client"""
        pass
    
    @abstractmethod
    async def close_all_connections(self) -> None:
        """Close all connections"""
        pass
    
    @abstractmethod
    async def handle_error(self, client_id: str, error_message: str) -> None:
        """Handle error"""
        pass


class IVectorStore(ABC):
    """Interface for vector store operations"""
    
    @abstractmethod
    async def get_query_embedding(self, query: str) -> List[float]:
        """Get embedding for query"""
        pass
    
    @abstractmethod
    async def search(
        self,
        company_id: str,
        query_embedding: List[float],
        current_agent_id: Optional[str] = None
    ) -> List[Dict]:
        """Search for similar documents"""
        pass
    
    @abstractmethod
    async def load_documents(
        self,
        company_id: str,
        agent_id: str,
        documents: List[Dict]
    ) -> None:
        """Load documents into vector store"""
        pass
    
    @abstractmethod
    async def delete_agent_data(self, company_id: str, agent_id: str) -> None:
        """Delete agent data from vector store"""
        pass


class IRAGService(ABC):
    """Interface for RAG service"""
    
    @abstractmethod
    async def create_qa_chain(
        self,
        company_id: str,
        agent_id: str,
        agent_prompt: str
    ) -> Any:
        """Create QA chain"""
        pass
    
    @abstractmethod
    async def get_answer_with_chain(
        self,
        chain: Any,
        question: str,
        conversation_context: List[Dict]
    ) -> Any:
        """Get answer using chain"""
        pass


class IBackgroundWorker(ABC):
    """Interface for background task processing"""
    
    @abstractmethod
    def enqueue(self, func, *args, **kwargs) -> None:
        """Enqueue background task"""
        pass
    
    @abstractmethod
    async def start(self) -> None:
        """Start background worker"""
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop background worker"""
        pass


class IMessageProcessor(ABC):
    """Interface for message processing"""
    
    @abstractmethod
    async def process_message(
        self,
        client_id: str,
        message_data: dict,
        conversation_manager: IConversationManager,
        agent_manager: IAgentManager,
        rag_service: IRAGService
    ) -> str:
        """Process message and return response"""
        pass


class IWebSocketHandler(ABC):
    """Interface for WebSocket handling"""
    
    @abstractmethod
    async def handle_connection(self, websocket: WebSocket, client_id: str) -> None:
        """Handle new WebSocket connection"""
        pass
    
    @abstractmethod
    async def handle_message(self, websocket: WebSocket, client_id: str, message: dict) -> None:
        """Handle incoming WebSocket message"""
        pass
    
    @abstractmethod
    async def handle_disconnection(self, client_id: str) -> None:
        """Handle WebSocket disconnection"""
        pass


class IConversationRepository(ABC):
    """Interface for conversation data access"""
    
    @abstractmethod
    async def get_conversation(self, conversation_id: str) -> Optional[Any]:
        """Get conversation by ID"""
        pass
    
    @abstractmethod
    async def create_conversation(self, conversation_data: Dict) -> Any:
        """Create new conversation"""
        pass
    
    @abstractmethod
    async def update_conversation(self, conversation_id: str, updates: Dict) -> bool:
        """Update conversation"""
        pass
    
    @abstractmethod
    async def get_conversation_history(self, conversation_id: str, limit: int = 10) -> List[Dict]:
        """Get conversation history"""
        pass


class IAgentRepository(ABC):
    """Interface for agent data access"""
    
    @abstractmethod
    async def get_agent(self, agent_id: str) -> Optional[Any]:
        """Get agent by ID"""
        pass
    
    @abstractmethod
    async def get_company_agents(self, company_id: str) -> List[Any]:
        """Get all agents for company"""
        pass
    
    @abstractmethod
    async def get_base_agent(self, company_id: str) -> Optional[Any]:
        """Get base agent for company"""
        pass
    
    @abstractmethod
    async def create_agent(self, agent_data: Dict) -> Any:
        """Create new agent"""
        pass
    
    @abstractmethod
    async def update_agent(self, agent_id: str, updates: Dict) -> bool:
        """Update agent"""
        pass


class IEventBus(ABC):
    """Interface for event bus"""
    
    @abstractmethod
    async def publish(self, event_type: str, event_data: Dict) -> None:
        """Publish event"""
        pass
    
    @abstractmethod
    async def subscribe(self, event_type: str, handler: callable) -> None:
        """Subscribe to event"""
        pass
    
    @abstractmethod
    async def unsubscribe(self, event_type: str, handler: callable) -> None:
        """Unsubscribe from event"""
        pass


class ILogger(ABC):
    """Interface for logging"""
    
    @abstractmethod
    def info(self, message: str, **kwargs) -> None:
        """Log info message"""
        pass
    
    @abstractmethod
    def error(self, message: str, **kwargs) -> None:
        """Log error message"""
        pass
    
    @abstractmethod
    def warning(self, message: str, **kwargs) -> None:
        """Log warning message"""
        pass
    
    @abstractmethod
    def debug(self, message: str, **kwargs) -> None:
        """Log debug message"""
        pass
