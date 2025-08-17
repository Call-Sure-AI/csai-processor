"""
Configuration settings for CSAI Processor
"""
import os
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings with environment variable support"""
    
    # Application
    app_name: str = Field(default="CSAI Processor", env="APP_NAME")
    app_version: str = Field(default="1.0.0", env="APP_VERSION")
    debug: bool = Field(default=False, env="DEBUG")
    
    # Server
    host: str = Field(default="0.0.0.0", env="HOST")
    port: int = Field(default=8001, env="PORT")
    workers: int = Field(default=1, env="WORKERS")
    
    # Database
    database_url: str = Field(..., env="DATABASE_URL")
    database_pool_size: int = Field(default=10, env="DATABASE_POOL_SIZE")
    database_max_overflow: int = Field(default=20, env="DATABASE_MAX_OVERFLOW")
    
    # Redis
    redis_url: str = Field(default="redis://localhost:6379", env="REDIS_DB_URL")
    
    # Vector Store
    qdrant_url: str = Field(default="http://localhost:6333", env="QDRANT_URL")
    qdrant_api_key: Optional[str] = Field(default=None, env="QDRANT_API_KEY")
    
    # OpenAI
    openai_api_key: str = Field(..., env="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4", env="OPENAI_MODEL")
    openai_max_tokens: int = Field(default=2000, env="OPENAI_MAX_TOKENS")
    openai_temperature: float = Field(default=0.7, env="OPENAI_TEMPERATURE")
    
    # WebSocket
    websocket_ping_interval: int = Field(default=20, env="WEBSOCKET_PING_INTERVAL")
    websocket_ping_timeout: int = Field(default=20, env="WEBSOCKET_PING_TIMEOUT")
    websocket_max_message_size: int = Field(default=1024 * 1024, env="WEBSOCKET_MAX_MESSAGE_SIZE")
    
    # Conversation
    conversation_cache_ttl: int = Field(default=1800, env="CONVERSATION_CACHE_TTL")  # 30 minutes
    conversation_max_history: int = Field(default=10, env="CONVERSATION_MAX_HISTORY")
    conversation_cleanup_interval: int = Field(default=3600, env="CONVERSATION_CLEANUP_INTERVAL")  # 1 hour
    
    # Agent
    agent_cache_ttl: int = Field(default=3600, env="AGENT_CACHE_TTL")  # 1 hour
    agent_default_confidence_threshold: float = Field(default=0.7, env="AGENT_DEFAULT_CONFIDENCE_THRESHOLD")
    
    # Rate Limiting
    rate_limit_requests: int = Field(default=100, env="RATE_LIMIT_REQUESTS")
    rate_limit_window: int = Field(default=60, env="RATE_LIMIT_WINDOW")  # seconds
    
    # Logging
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_format: str = Field(default="json", env="LOG_FORMAT")
    
    # CORS
    allowed_origins: Optional[str] = Field(default=None, env="ALLOWED_ORIGINS")
    
    # Security
    secret_key: str = Field(default="your-secret-key-change-in-production", env="SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", env="JWT_ALGORITHM")
    jwt_expiration: int = Field(default=3600, env="JWT_EXPIRATION")  # 1 hour
    
    # Background Tasks
    background_worker_enabled: bool = Field(default=True, env="BACKGROUND_WORKER_ENABLED")
    background_worker_concurrency: int = Field(default=4, env="BACKGROUND_WORKER_CONCURRENCY")
    
    # Monitoring
    enable_metrics: bool = Field(default=True, env="ENABLE_METRICS")
    metrics_port: int = Field(default=9090, env="METRICS_PORT")
    
    # WebRTC
    enable_webrtc: bool = Field(default=False, env="ENABLE_WEBRTC")
    webrtc_ice_servers: Optional[str] = Field(default=None, env="WEBRTC_ICE_SERVERS")
    webrtc_max_message_size: int = Field(default=1048576, env="WEBRTC_MAX_MESSAGE_SIZE")
    webrtc_heartbeat_interval: int = Field(default=30, env="WEBRTC_HEARTBEAT_INTERVAL")
    webrtc_connection_timeout: int = Field(default=300, env="WEBRTC_CONNECTION_TIMEOUT")
    webrtc_max_connections_per_company: int = Field(default=100, env="WEBRTC_MAX_CONNECTIONS_PER_COMPANY")
    
    # Audio Processing
    audio_chunk_size: int = Field(default=32768, env="AUDIO_CHUNK_SIZE")
    audio_max_text_length: int = Field(default=500, env="AUDIO_MAX_TEXT_LENGTH")
    audio_cache_ttl: int = Field(default=3600, env="AUDIO_CACHE_TTL")
    audio_chunk_delay: float = Field(default=0.01, env="AUDIO_CHUNK_DELAY")
    
    # AI Services
    claude_api_key: Optional[str] = Field(default=None, env="CLAUDE_API_KEY")
    deepgram_api_key: Optional[str] = Field(default=None, env="DEEPGRAM_API_KEY")
    eleven_labs_api_key: Optional[str] = Field(default=None, env="ELEVEN_LABS_API_KEY")
    voice_id: Optional[str] = Field(default=None, env="VOICE_ID")
    
    # Twilio
    twilio_account_sid: Optional[str] = Field(default=None, env="TWILIO_ACCOUNT_SID")
    twilio_auth_token: Optional[str] = Field(default=None, env="TWILIO_AUTH_TOKEN")
    twilio_phone_number: Optional[str] = Field(default=None, env="TWILIO_PHONE_NUMBER")
    
    # Twilio Voice Settings
    twilio_webhook_timeout: int = Field(default=30, env="TWILIO_WEBHOOK_TIMEOUT")
    twilio_max_call_duration: int = Field(default=3600, env="TWILIO_MAX_CALL_DURATION")  # 1 hour
    twilio_enable_recording: bool = Field(default=False, env="TWILIO_ENABLE_RECORDING")
    twilio_recording_status_callback: Optional[str] = Field(default=None, env="TWILIO_RECORDING_STATUS_CALLBACK")
    
    # Call Management
    call_cleanup_interval: int = Field(default=3600, env="CALL_CLEANUP_INTERVAL")  # 1 hour
    max_concurrent_calls: int = Field(default=100, env="MAX_CONCURRENT_CALLS")
    
    # Default Configuration
    default_company_api_key: Optional[str] = Field(default=None, env="DEFAULT_COMPANY_API_KEY")
    default_agent_id: Optional[str] = Field(default=None, env="DEFAULT_AGENT_ID")
    
    # AWS
    aws_access_key_id: Optional[str] = Field(default=None, env="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: Optional[str] = Field(default=None, env="AWS_SECRET_ACCESS_KEY")
    
    # Exotel
    exotel_sid: Optional[str] = Field(default=None, env="EXOTEL_SID")
    exotel_token: Optional[str] = Field(default=None, env="EXOTEL_TOKEN")
    exotel_api_key: Optional[str] = Field(default=None, env="EXOTEL_API_KEY")
    
    # Scheduler
    scheduler_interval_minutes: int = Field(default=2, env="SCHEDULER_INTERVAL_MINUTES")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"  # Ignore extra fields from .env
        
    @property
    def cors_allowed_origins(self) -> List[str]:
        """Get allowed origins for CORS, with fallback to default"""
        if self.allowed_origins is None:
            return ["*"]
        # Parse comma-separated string into list
        return [origin.strip() for origin in self.allowed_origins.split(",")]


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()


# Global settings instance
settings = get_settings()
