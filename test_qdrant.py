# test_qdrant.py
from qdrant_client import QdrantClient
from dotenv import load_dotenv
import os

load_dotenv()

host = os.getenv("QDRANT_HOST")
port = os.getenv("QDRANT_PORT", 6333)
api_key = os.getenv("QDRANT_API_KEY")

print(f"Testing connection to {host}:{port}")

try:
    client = QdrantClient(
        url=f"http://{host}:{port}",
        api_key=api_key,
        timeout=10
    )
    
    collections = client.get_collections()
    print(f"✅ Connected! Collections: {collections}")
except Exception as e:
    print(f"❌ Connection failed: {e}")
