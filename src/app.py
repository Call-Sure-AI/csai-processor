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
from routes.celery_twilio_routes import router as celery_twilio_router
from routes.twilio_webhook_routes import router as twilio_webhook_router
from routes.elevenlabs_twilio_routes import router as elevenlabs_twilio_router, elevenlabs_integration_lifespan
from routes.webrtc_elevenlabs_routes import router as webrtc_elevenlabs_router, webrtc_lifespan
from routes.elevenlabs_twilio_websocket_routes import router as elevenlabs_twilio_websocket_router
from routes.s3 import router as s3_router
from routes.campaign_routes import router as campaign_router
from routes.document_routes import router as document_router
from routes.outbound_routes import router as outbound_router
from routes.twilio_elevenlabs_routes import router as twilio_elevenlabs_router

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
    """Combined application lifespan manager"""
    # Startup sequence
    logger.info("Starting application lifespan...")
    
    # Start main application services
    await startup_event()
    
    # Start ElevenLabs and WebRTC services using their lifespan managers
    try:
        async with elevenlabs_integration_lifespan(app):
            async with webrtc_lifespan(app):
                logger.info("All services started successfully")
                yield
    except Exception as e:
        logger.error(f"Error in service lifespan management: {str(e)}")
        raise
    finally:
        # Shutdown sequence
        await shutdown_event()
        logger.info("Application lifespan completed")

def create_app() -> FastAPI:
    """Create and configure FastAPI application"""
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        description="CSAI Processor - Real-time conversation and WebSocket processing service with ElevenLabs Twilio Integration",
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
        try:
            # Check if critical services are initialized
            services_status = {
                "connection_manager": connection_manager is not None,
                "background_worker": background_worker is not None,
                "conversation_manager": conversation_manager is not None,
                "agent_manager": agent_manager is not None,
                "webrtc_manager": webrtc_manager is not None,
                "vector_store": vector_store is not None,
                "rag_service": rag_service is not None,
                "message_processor": message_processor is not None
            }
            
            all_healthy = all(services_status.values())
            
            return {
                "status": "healthy" if all_healthy else "degraded",
                "service": settings.app_name,
                "version": settings.app_version,
                "timestamp": asyncio.get_event_loop().time(),
                "services": services_status,
                "elevenlabs_configured": bool(getattr(settings, 'eleven_labs_api_key', None)),
                "twilio_configured": bool(getattr(settings, 'twilio_account_sid', None) and getattr(settings, 'twilio_auth_token', None))
            }
        except Exception as e:
            logger.error(f"Health check failed: {str(e)}")
            return {
                "status": "unhealthy",
                "service": settings.app_name,
                "error": str(e),
                "timestamp": asyncio.get_event_loop().time()
            }

    # Using elevenlabs for incoming call
    app.include_router(twilio_elevenlabs_router, tags=["Elevenlabs-Twilio"])
    app.include_router(outbound_router, tags=["Outbound Calls"])
    
    app.include_router(document_router, prefix="/api/v1/documents", tags=["documents"])
    # Include WebRTC routes
    app.include_router(webrtc_router, prefix="/api/v1/webrtc", tags=["WebRTC"])

    app.include_router(s3_router, prefix="/api/v1/s3", tags=["S3"])
    
    # Include Twilio routes
    app.include_router(twilio_router, prefix="/api/v1/twilio", tags=["Twilio Voice"])
    
    # Include Celery Twilio routes
    app.include_router(celery_twilio_router, prefix="/api/v1/celery/twilio", tags=["Celery Twilio Calls"])
    
    # Include Twilio Webhook routes
    app.include_router(twilio_webhook_router, prefix="/api/v1/twilio/webhook", tags=["Twilio Webhooks"])
    
    # Include ElevenLabs Twilio integration routes
    app.include_router(elevenlabs_twilio_router, tags=["ElevenLabs Twilio Integration"])
    
    # Include WebRTC ElevenLabs integration routes
    app.include_router(webrtc_elevenlabs_router, tags=["WebRTC ElevenLabs"])
    
    # Include ElevenLabs Twilio WebSocket routes
    app.include_router(elevenlabs_twilio_websocket_router, tags=["ElevenLabs WebSocket"])
    
    # Campaign router
    app.include_router(campaign_router, prefix="/campaign", tags=["Campaign Management"])
    
    # WebSocket endpoint
    @app.websocket("/ws/{client_id}")
    async def websocket_endpoint(websocket: WebSocket, client_id: str):
        await handle_websocket_connection(websocket, client_id)

    # Statistics endpoint
    @app.get("/stats")
    async def get_stats():
        try:
            if not connection_manager or not background_worker:
                return {"error": "Core services not initialized"}
            
            stats = {
                "connection_stats": connection_manager.get_connection_stats(),
                "background_worker_stats": background_worker.get_stats(),
                "service_info": {
                    "name": settings.app_name,
                    "version": settings.app_version,
                    "uptime": asyncio.get_event_loop().time()
                }
            }
            
            # Add WebRTC stats if available
            if webrtc_manager:
                try:
                    webrtc_stats = webrtc_manager.get_connection_stats()
                    stats["webrtc_stats"] = webrtc_stats
                except Exception as e:
                    logger.warning(f"Failed to get WebRTC stats: {str(e)}")
                    stats["webrtc_stats"] = {"error": "Failed to retrieve stats"}
            
            return stats
            
        except Exception as e:
            logger.error(f"Failed to get stats: {str(e)}")
            return {"error": f"Failed to retrieve statistics: {str(e)}"}

    # ElevenLabs integration status endpoint
    @app.get("/elevenlabs/status")
    async def elevenlabs_status():
        try:
            # Import here to avoid circular imports
            from routes.elevenlabs_twilio_routes import integration_initialized
            from services.voice.twilio_elevenlabs_integration import twilio_elevenlabs_integration
            
            return {
                "integration_initialized": integration_initialized,
                "elevenlabs_api_configured": bool(getattr(settings, 'eleven_labs_api_key', None)),
                "twilio_configured": bool(getattr(settings, 'twilio_account_sid', None)),
                "active_integrations": twilio_elevenlabs_integration.get_active_integrations_count() if integration_initialized else 0,
                "voice_service_available": hasattr(settings, 'voice_id') and bool(settings.voice_id),
                "webhook_url_configured": bool(getattr(settings, 'base_url', None) or getattr(settings, 'webhook_base_url', None))
            }
        except Exception as e:
            logger.error(f"Failed to get ElevenLabs status: {str(e)}")
            return {"error": f"Failed to retrieve ElevenLabs status: {str(e)}"}

    return app

async def startup_event():
    """Initialize application on startup"""
    global connection_manager, background_worker, conversation_manager, agent_manager, webrtc_manager, vector_store, rag_service, message_processor
    qdrant_service = QdrantService()

    try:
        logger.info(f"Qdrant Host: {settings.qdrant_host}")
        logger.info(f"Qdrant Port: {settings.qdrant_port}")
        logger.info(f"Qdrant URL: {settings.qdrant_url}")
        logger.info(f"Qdrant Collection: {settings.qdrant_collection_name}")
        await qdrant_service.initialize_collection()
        logger.info("Qdrant collection initialized")

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

        # Validate ElevenLabs configuration
        if hasattr(settings, 'eleven_labs_api_key') and settings.eleven_labs_api_key:
            logger.info("ElevenLabs API key configured")
        else:
            logger.warning("ElevenLabs API key not configured - voice services will be limited")

        # Validate Twilio configuration
        if (hasattr(settings, 'twilio_account_sid') and settings.twilio_account_sid and 
            hasattr(settings, 'twilio_auth_token') and settings.twilio_auth_token):
            logger.info("Twilio credentials configured")
        else:
            logger.warning("Twilio credentials not configured - call services will be limited")

        logger.info("CSAI Processor core services startup complete")

    except Exception as e:
        logger.critical(f"Failed to initialize application: {str(e)}", exc_info=True)
        raise

async def shutdown_event():
    """Cleanup on application shutdown"""
    try:
        logger.info("Shutting down CSAI Processor...")
        
        # Stop background worker
        if background_worker:
            try:
                await background_worker.stop()
                logger.info("Background worker stopped")
            except Exception as e:
                logger.error(f"Error stopping background worker: {str(e)}")
        
        # Close all WebSocket connections
        if connection_manager:
            try:
                await connection_manager.close_all_connections()
                logger.info("All connections closed")
            except Exception as e:
                logger.error(f"Error closing connections: {str(e)}")
        
        # Close WebRTC connections
        if webrtc_manager:
            try:
                await webrtc_manager.close_all_connections()
                logger.info("All WebRTC connections closed")
            except Exception as e:
                logger.error(f"Error closing WebRTC connections: {str(e)}")
        
        # Close database connections
        try:
            close_database()
            logger.info("Database connections closed")
        except Exception as e:
            logger.error(f"Error closing database connections: {str(e)}")
        
        logger.info("CSAI Processor shutdown complete")
        
    except Exception as e:
        logger.error(f"Error during application shutdown: {str(e)}")

async def handle_websocket_connection(websocket: WebSocket, client_id: str):
    """Handle WebSocket connection lifecycle"""
    try:
        # Check if connection manager is available
        if not connection_manager:
            logger.error("Connection manager not initialized")
            await websocket.close(code=1011, reason="Service not available")
            return
        
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
                try:
                    await connection_manager.handle_error(client_id, str(e))
                except Exception as error_handling_error:
                    logger.error(f"Error handling error for client {client_id}: {str(error_handling_error)}")
                break
                
    except Exception as e:
        logger.error(f"Error in WebSocket connection for client {client_id}: {str(e)}")
    finally:
        # Cleanup connection
        if connection_manager:
            try:
                connection_manager.disconnect(client_id)
            except Exception as e:
                logger.error(f"Error disconnecting client {client_id}: {str(e)}")

app = create_app()

if __name__ == "__main__":
    import uvicorn
    
    # Validate required environment variables before starting
    required_settings = ['app_name', 'host', 'port']
    missing_settings = [setting for setting in required_settings if not hasattr(settings, setting)]
    
    if missing_settings:
        logger.error(f"Missing required settings: {missing_settings}")
        exit(1)
    
    # Log startup configuration
    logger.info(f"Starting {settings.app_name} v{getattr(settings, 'app_version', 'unknown')}")
    logger.info(f"Host: {settings.host}:{settings.port}")
    logger.info(f"Debug mode: {getattr(settings, 'debug', False)}")
    logger.info(f"Workers: {getattr(settings, 'workers', 1)}")
    
    # Warn about missing optional configurations
    if not hasattr(settings, 'eleven_labs_api_key') or not settings.eleven_labs_api_key:
        logger.warning("ElevenLabs API key not configured - voice synthesis will not work")
    
    if not hasattr(settings, 'twilio_account_sid') or not settings.twilio_account_sid:
        logger.warning("Twilio account SID not configured - call services will not work")
    
    uvicorn.run(
        "app:app",
        host=settings.host,
        port=settings.port,
        reload=getattr(settings, 'debug', False),
        workers=getattr(settings, 'workers', 1)
    )