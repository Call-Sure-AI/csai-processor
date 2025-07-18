# services/data_loaders.py

import os
from typing import List, Dict, Any, Optional
import pandas as pd
from sqlalchemy import create_engine, inspect
import logging
from datetime import datetime
import asyncio
import aiofiles
from pathlib import Path
import magic  # for file type detection
import textract
from bs4 import BeautifulSoup
import json

logger = logging.getLogger(__name__)

class DocumentLoader:
    """Handles loading and processing various document types"""
    
    SUPPORTED_EXTENSIONS = {
        '.txt', '.pdf', '.docx', '.doc', '.html', 
        '.md', '.json', '.csv', '.xlsx'
    }
    
    def __init__(self):
        self.mime = magic.Magic(mime=True)
    
    async def load_document(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Load and process a document file"""
        try:
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"File not found: {file_path}")
                
            file_ext = Path(file_path).suffix.lower()
            if file_ext not in self.SUPPORTED_EXTENSIONS:
                raise ValueError(f"Unsupported file type: {file_ext}")
            
            # Get file mime type
            mime_type = self.mime.from_file(file_path)
            
            # Extract text based on file type
            if file_ext in ['.txt', '.md']:
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
            
            elif file_ext == '.pdf':
                content = await asyncio.to_thread(
                    textract.process,
                    file_path,
                    method='pdfminer'
                )
                content = content.decode('utf-8')
            
            elif file_ext in ['.docx', '.doc']:
                content = await asyncio.to_thread(
                    textract.process,
                    file_path
                )
                content = content.decode('utf-8')
            
            elif file_ext == '.html':
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    html_content = await f.read()
                soup = BeautifulSoup(html_content, 'html.parser')
                content = soup.get_text(separator=' ')
            
            elif file_ext == '.json':
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    json_content = await f.read()
                content = json.dumps(json.loads(json_content))
            
            elif file_ext in ['.csv', '.xlsx']:
                if file_ext == '.csv':
                    df = pd.read_csv(file_path)
                else:
                    df = pd.read_excel(file_path)
                content = df.to_string()
            
            return {
                'id': os.path.basename(file_path),
                'content': content,
                'metadata': {
                    'filename': os.path.basename(file_path),
                    'file_type': file_ext,
                    'mime_type': mime_type,
                    'size': os.path.getsize(file_path),
                    'last_modified': datetime.fromtimestamp(
                        os.path.getmtime(file_path)
                    ).isoformat()
                },
                'doc_type': 'document'
            }
            
        except Exception as e:
            logger.error(f"Error loading document {file_path}: {str(e)}")
            return None

    async def load_directory(
        self,
        directory_path: str,
        recursive: bool = True
    ) -> List[Dict[str, Any]]:
        """Load all supported documents from a directory"""
        documents = []
        try:
            for root, _, files in os.walk(directory_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    if Path(file).suffix.lower() in self.SUPPORTED_EXTENSIONS:
                        doc = await self.load_document(file_path)
                        if doc:
                            documents.append(doc)
                
                if not recursive:
                    break
                    
            return documents
            
        except Exception as e:
            logger.error(f"Error loading directory {directory_path}: {str(e)}")
            return []

class DatabaseLoader:
    """Handles loading and processing database tables"""
    
    def __init__(self):
        self.supported_databases = {
            'postgresql': 'postgresql://',
            'mysql': 'mysql://',
            'mssql': 'mssql://',
            'oracle': 'oracle://',
            'sqlite': 'sqlite:///'
        }
    
    async def create_connection(
        self,
        db_type: str,
        connection_params: Dict[str, Any]
    ) -> Any:
        """Create database connection"""
        try:
            if db_type not in self.supported_databases:
                raise ValueError(f"Unsupported database type: {db_type}")
            
            # Construct connection URL based on database type
            if db_type == 'sqlite':
                connection_url = f"{self.supported_databases[db_type]}{connection_params['database']}"
            else:
                connection_url = (
                    f"{self.supported_databases[db_type]}"
                    f"{connection_params.get('username', '')}:"
                    f"{connection_params.get('password', '')}@"
                    f"{connection_params.get('host', 'localhost')}:"
                    f"{connection_params.get('port', '')}/"
                    f"{connection_params.get('database', '')}"
                )
            
            # Create engine
            engine = create_engine(connection_url)
            
            return engine
            
        except Exception as e:
            logger.error(f"Error creating database connection: {str(e)}")
            raise

    async def get_table_schema(self, engine: Any, table_name: str) -> Dict[str, Any]:
        """Get schema information for a specific table"""
        try:
            inspector = inspect(engine)
            
            if table_name not in inspector.get_table_names():
                raise ValueError(f"Table {table_name} not found in database")
            
            columns = inspector.get_columns(table_name)
            primary_keys = inspector.get_primary_keys(table_name)
            foreign_keys = inspector.get_foreign_keys(table_name)
            
            return {
                'columns': columns,
                'primary_keys': primary_keys,
                'foreign_keys': foreign_keys
            }
            
        except Exception as e:
            logger.error(f"Error getting schema for table {table_name}: {str(e)}")
            raise

    async def load_table_data(
        self,
        engine: Any,
        table_name: str,
        batch_size: int = 1000,
        custom_query: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Load data from a database table"""
        try:
            # Get total count for batching
            with engine.connect() as conn:
                if custom_query:
                    query = custom_query
                else:
                    query = f"SELECT * FROM {table_name}"
                
                # Execute query in batches
                df = pd.read_sql(
                    query,
                    conn,
                    chunksize=batch_size
                )
                
                data = []
                for chunk in df:
                    records = chunk.to_dict('records')
                    data.extend(records)
                
                return data
                
        except Exception as e:
            logger.error(f"Error loading data from table {table_name}: {str(e)}")
            raise

    async def process_table_data(
        self,
        table_data: List[Dict[str, Any]],
        schema_mapping: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        """Process table data into a format suitable for vector storage"""
        try:
            processed_data = []
            
            for record in table_data:
                # Create semantic text representation
                semantic_parts = []
                
                for col_name, semantic_name in schema_mapping.items():
                    if col_name in record:
                        value = record[col_name]
                        if pd.notna(value):  # Handle NULL/NaN values
                            semantic_parts.append(f"{semantic_name}: {value}")
                
                if semantic_parts:
                    processed_data.append({
                        'id': str(record.get('id', hash(str(record)))),
                        'content': '. '.join(semantic_parts),
                        'metadata': {
                            'original_record': record,
                            'schema_mapping': schema_mapping
                        },
                        'doc_type': 'database_record'
                    })
            
            return processed_data
            
        except Exception as e:
            logger.error(f"Error processing table data: {str(e)}")
            raise

    async def validate_connection(self, engine: Any) -> bool:
        """Validate database connection"""
        try:
            with engine.connect() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception as e:
            logger.error(f"Connection validation failed: {str(e)}")
            return False

    async def load_database(
        self,
        connection_params: Dict[str, Any],
        tables_config: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Load and process multiple tables from a database
        
        tables_config format:
        [
            {
                'table_name': 'customers',
                'schema_mapping': {'id': 'Customer ID', 'name': 'Customer Name', ...},
                'custom_query': 'SELECT * FROM customers WHERE active = true'  # Optional
            },
            ...
        ]
        """
        try:
            engine = await self.create_connection(
                connection_params['type'],
                connection_params
            )
            
            if not await self.validate_connection(engine):
                raise Exception("Failed to validate database connection")
            
            all_data = []
            
            for table_config in tables_config:
                table_name = table_config['table_name']
                schema_mapping = table_config['schema_mapping']
                custom_query = table_config.get('custom_query')
                
                # Load raw data
                raw_data = await self.load_table_data(
                    engine,
                    table_name,
                    custom_query=custom_query
                )
                
                # Process data
                processed_data = await self.process_table_data(
                    raw_data,
                    schema_mapping
                )
                
                all_data.extend(processed_data)
            
            return all_data
            
        except Exception as e:
            logger.error(f"Error loading database: {str(e)}")
            raise
        finally:
            if 'engine' in locals():
                engine.dispose()