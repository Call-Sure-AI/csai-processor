"""
Main entry point for CSAI Processor
"""
import uvicorn
import sys
from pathlib import Path

# Add the project root to the Python path
root_path = str(Path(__file__).parent)
if root_path not in sys.path:
    sys.path.append(root_path)

from app import app
from config.settings import settings

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        workers=settings.workers
    )
