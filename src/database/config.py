from sqlalchemy import create_engine, event, func
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from sqlalchemy.pool import QueuePool
from contextlib import contextmanager, asynccontextmanager
from typing import Generator, Callable, Any
import logging
import os
import signal
import sys
from dotenv import load_dotenv
from config.settings import settings
from sqlalchemy import text


logger = logging.getLogger(__name__)


Base = declarative_base()


load_dotenv()


database_url = os.getenv("DATABASE_URL")
engine = create_engine(database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class DatabaseClient:
    def __init__(self):
        self.engine = None
        self.SessionLocal = None
        self._initialized = False
    
    def initialize(self):
        """Initialize the database connection pool"""
        if self._initialized:
            return
            
        try:
            self.engine = create_engine(
                settings.database_url,
                poolclass=QueuePool,
                pool_size=settings.database_pool_size,
                max_overflow=settings.database_max_overflow,
                pool_pre_ping=True,
                pool_recycle=3600,
                echo=settings.debug
            )

            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                logger.info("Database connection established successfully")

            self.SessionLocal = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=self.engine
            )
            
            self._initialized = True           
        except Exception as e:
            logger.error(f"Failed to connect to database: {str(e)}")
            raise
    
    def query(self, text: str, params: list = None):
        """
        Execute a raw SQL query (similar to JS version)
        """
        if not self._initialized:
            raise Exception("Database not initialized. Call initialize() first.")
        
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text, params or [])
                if result.returns_rows:
                    return [dict(row) for row in result]
                return []
        except Exception as e:
            logger.error(f"Database query error: {str(e)}")
            raise
    
    @contextmanager
    def get_client(self):
        """
        Get a connection for transactions (similar to JS getClient)
        """
        if not self._initialized:
            raise Exception("Database not initialized. Call initialize() first.")
        
        conn = self.engine.connect()
        try:
            yield conn
        finally:
            conn.close()
    
    @contextmanager
    def transaction(self, callback: Callable = None):
        """
        Execute a transaction (similar to JS version)
        """
        with self.get_client() as client:
            trans = client.begin()
            try:
                if callback:
                    result = callback(client)
                    trans.commit()
                    return result
                else:
                    yield client
                    trans.commit()
            except Exception as e:
                trans.rollback()
                raise
    
    def close(self):
        """Close the database connection pool"""
        if self.engine:
            self.engine.dispose()
            logger.info("🔌 Database connection pool closed")


class DatabaseManager:
    """Original database connection and session manager"""
    
    def __init__(self):
        self.engine = None
        self.SessionLocal = None
        self._initialized = False
    
    def initialize(self):
        """Initialize database connection"""
        if self._initialized:
            return
            
        try:
            self.engine = create_engine(
                settings.database_url,
                poolclass=QueuePool,
                pool_size=settings.database_pool_size,
                max_overflow=settings.database_max_overflow,
                pool_pre_ping=True,
                pool_recycle=3600,
                echo=settings.debug
            )
            
            self.SessionLocal = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=self.engine
            )
            
            self._initialized = True
            logger.info("Database initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize database: {str(e)}")
            raise
    
    def get_session(self) -> Session:
        """Get a new database session"""
        if not self._initialized:
            self.initialize()
        return self.SessionLocal()
    
    @contextmanager
    def get_session_context(self) -> Generator[Session, None, None]:
        """Context manager for database sessions"""
        session = self.get_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    
    def close(self):
        """Close database connections"""
        if self.engine:
            self.engine.dispose()
            logger.info("Database connections closed")


db_manager = DatabaseManager()
db = DatabaseClient()

def handle_shutdown_signal(signum, frame):
    """Handle SIGINT and SIGTERM for graceful shutdown"""
    logger.info(f"Received signal {signum}, closing database connections...")
    db.close()
    db_manager.close()
    sys.exit(0)


signal.signal(signal.SIGINT, handle_shutdown_signal)
signal.signal(signal.SIGTERM, handle_shutdown_signal)


def get_db() -> Generator[Session, None, None]:
    """Dependency function for FastAPI to get database session"""
    with db_manager.get_session_context() as session:
        yield session


def init_database():
    """Initialize database on startup"""
    db_manager.initialize()
    db.initialize()


def close_database():
    """Close database on shutdown"""
    db_manager.close()
    db.close()
