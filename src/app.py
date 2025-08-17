"""
Main FastAPI application for CSAI Processor
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import RedirectResponse
import logging
import asyncio
from contextlib import asynccontextmanager

from config.settings import settings
from database.config import init_database, close_database, get_db
from database.models import Base
from managers.conversation_manager import ConversationManager
from managers.agent_manager import AgentManager
from managers.connection_manager import ConnectionManager
from managers.webrtc_manager import WebRTCManager
from processors.message_processor import MessageProcessor, StreamingMessageProcessor
from workers.background_worker import BackgroundWorker
from services.vector_store.qdrant_service import QdrantService
from services.rag.rag_service import RAGService
from routes.webrtc_routes import router as webrtc_router, initialize_webrtc_services
from routes.twilio_routes import router as twilio_router


# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Global instances (will be initialized in startup)
connection_manager: ConnectionManager = None
background_worker: BackgroundWorker = None
conversation_manager: ConversationManager = None
agent_manager: AgentManager = None
webrtc_manager: WebRTCManager = None
vector_store: QdrantService = None
rag_service: RAGService = None
message_processor: MessageProcessor = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    await startup_event()
    yield
    # Shutdown
    await shutdown_event()


def create_app() -> FastAPI:
    """Create and configure FastAPI application"""
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        description="CSAI Processor - Real-time conversation and WebSocket processing service",
        lifespan=lifespan
    )

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"]
    )

    # Root route redirects to docs
    @app.get("/")
    async def root():
        return RedirectResponse(url="/docs")

    # Health check endpoint
    @app.get("/health")
    async def health_check():
        return {
            "status": "healthy",
            "service": settings.app_name,
            "version": settings.app_version,
            "timestamp": asyncio.get_event_loop().time()
        }

    # Include WebRTC routes
    app.include_router(webrtc_router, prefix="/api/v1/webrtc", tags=["WebRTC"])
    
    # Include Twilio routes
    app.include_router(twilio_router, prefix="/api/v1/twilio", tags=["Twilio Voice"])
    
    # WebSocket endpoint
    @app.websocket("/ws/{client_id}")
    async def websocket_endpoint(websocket: WebSocket, client_id: str):
        await handle_websocket_connection(websocket, client_id)

    # Statistics endpoint
    @app.get("/stats")
    async def get_stats():
        if not connection_manager or not background_worker:
            return {"error": "Services not initialized"}
        
        return {
            "connection_stats": connection_manager.get_connection_stats(),
            "background_worker_stats": background_worker.get_stats(),
            "service_info": {
                "name": settings.app_name,
                "version": settings.app_version,
                "uptime": asyncio.get_event_loop().time()
            }
        }

    return app


async def startup_event():
    """Initialize application on startup"""
    global connection_manager, background_worker, conversation_manager, agent_manager, webrtc_manager, vector_store, rag_service, message_processor
    
    try:
        logger.info("Starting CSAI Processor...")
        
        # Initialize database
        init_database()
        logger.info("Database initialized")

        # Initialize vector store
        vector_store = QdrantService()
        logger.info("Vector store initialized")

        # Initialize RAG service
        rag_service = RAGService(vector_store)
        logger.info("RAG service initialized")

        # Initialize background worker
        background_worker = BackgroundWorker()
        await background_worker.start()
        logger.info("Background worker started")

        # Get database session for managers
        db_session = next(get_db())
        
        # Initialize conversation manager
        conversation_manager = ConversationManager(
            db_session=db_session,
            background_worker=background_worker
        )
        logger.info("Conversation manager initialized")

        # Initialize agent manager
        agent_manager = AgentManager(
            db_session=db_session,
            vector_store=vector_store
        )
        logger.info("Agent manager initialized")

        # Initialize message processor
        message_processor = StreamingMessageProcessor()
        logger.info("Message processor initialized")

        # Initialize connection manager
        connection_manager = ConnectionManager(
            db_session=db_session,
            agent_manager=agent_manager,
            vector_store=vector_store,
            rag_service=rag_service,
            message_processor=message_processor,
            conversation_manager=conversation_manager
        )
        logger.info("Connection manager initialized")

        # Initialize WebRTC manager
        webrtc_manager = WebRTCManager()
        webrtc_manager.initialize_services(db_session, vector_store)
        logger.info("WebRTC manager initialized")

        # Initialize WebRTC services in routes
        initialize_webrtc_services(webrtc_manager, vector_store, connection_manager)
        logger.info("WebRTC services initialized")

        logger.info("CSAI Processor startup complete")

    except Exception as e:
        logger.critical(f"Failed to initialize application: {str(e)}", exc_info=True)
        raise


async def shutdown_event():
    """Cleanup on application shutdown"""
    try:
        logger.info("Shutting down CSAI Processor...")
        
        # Stop background worker
        if background_worker:
            await background_worker.stop()
            logger.info("Background worker stopped")
        
        # Close all WebSocket connections
        if connection_manager:
            await connection_manager.close_all_connections()
            logger.info("All connections closed")
        
        # Close WebRTC connections
        if webrtc_manager:
            await webrtc_manager.close_all_connections()
            logger.info("All WebRTC connections closed")
        
        # Close database connections
        close_database()
        logger.info("Database connections closed")
        
        logger.info("CSAI Processor shutdown complete")
        
    except Exception as e:
        logger.error(f"Error during application shutdown: {str(e)}")


async def handle_websocket_connection(websocket: WebSocket, client_id: str):
    """Handle WebSocket connection lifecycle"""
    try:
        # Accept connection
        await websocket.accept()
        logger.info(f"WebSocket connection accepted for client {client_id}")
        
        # Connect to connection manager
        await connection_manager.connect(websocket, client_id)
        
        # Send welcome message
        await connection_manager.send_welcome_message(client_id)
        
        # Handle messages
        while True:
            try:
                # Receive message
                data = await websocket.receive_json()
                
                # Process message
                await connection_manager.process_message(client_id, data)
                
            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected for client {client_id}")
                break
            except Exception as e:
                logger.error(f"Error handling message for client {client_id}: {str(e)}")
                await connection_manager.handle_error(client_id, str(e))
                break
                
    except Exception as e:
        logger.error(f"Error in WebSocket connection for client {client_id}: {str(e)}")
    finally:
        # Cleanup connection
        connection_manager.disconnect(client_id)


# Create application instance
app = create_app()


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        workers=settings.workers
    )
