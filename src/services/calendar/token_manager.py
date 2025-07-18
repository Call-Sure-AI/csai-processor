import os
import json
import logging
import threading
import platform
from datetime import datetime, UTC
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class TokenManager:
    """Cross-platform token storage and management"""
    def __init__(self, token_store_path: Optional[str]):
        self.token_store_path = token_store_path or os.path.expanduser('~/.calendar_tokens')
        self.lock = threading.Lock()
        self.is_windows = platform.system().lower() == 'windows'
        
        # Create directory if it doesn't exist
        directory = os.path.dirname(self.token_store_path)
        if directory:  # Only create if there's a directory part
            os.makedirs(directory, exist_ok=True)
        
        # Import platform-specific modules
        if self.is_windows:
            import msvcrt
            self.msvcrt = msvcrt
        else:
            import fcntl
            self.fcntl = fcntl

    def _acquire_lock(self, file_obj) -> bool:
        """Try to acquire a file lock using platform-specific implementation"""
        try:
            if self.is_windows:
                handle = self.msvcrt.get_osfhandle(file_obj.fileno())
                # Try to lock only the first byte
                self.msvcrt.locking(file_obj.fileno(), self.msvcrt.LK_NBLCK, 1)
            else:
                self.fcntl.flock(file_obj.fileno(), self.fcntl.LOCK_EX | self.fcntl.LOCK_NB)
            return True
        except (IOError, OSError) as e:
            logger.debug(f"Could not acquire lock: {e}")
            return False

    def _release_lock(self, file_obj):
        """Release the file lock using platform-specific implementation"""
        try:
            if self.is_windows:
                # Reset file pointer to start before unlocking
                file_obj.seek(0)
                self.msvcrt.locking(file_obj.fileno(), self.msvcrt.LK_UNLCK, 1)
            else:
                self.fcntl.flock(file_obj.fileno(), self.fcntl.LOCK_UN)
        except (IOError, OSError) as e:
            # Log as debug since this isn't critical - the lock will be released when the file is closed anyway
            logger.debug(f"Error releasing file lock: {e}")

    def save_token(self, service: str, token_data: Dict[str, Any]) -> None:
        """Save token with file locking"""
        with self.lock:
            # Create file if it doesn't exist
            if not os.path.exists(self.token_store_path):
                open(self.token_store_path, 'a').close()
                
            with open(self.token_store_path, 'r+') as f:
                if not self._acquire_lock(f):
                    raise OSError("Could not acquire lock for token file")
                try:
                    # Read existing content
                    f.seek(0)
                    try:
                        tokens = json.load(f)
                    except json.JSONDecodeError:
                        tokens = {}
                    
                    # Update tokens
                    tokens[service] = {
                        'data': token_data,
                        'updated_at': datetime.now(UTC).isoformat()
                    }
                    
                    # Write back
                    f.seek(0)
                    f.truncate()
                    json.dump(tokens, f)
                finally:
                    self._release_lock(f)

    def load_token(self, service: str) -> Optional[Dict[str, Any]]:
        """Load token with file locking"""
        with self.lock:
            if not os.path.exists(self.token_store_path):
                return None

            with open(self.token_store_path, 'r') as f:
                if not self._acquire_lock(f):
                    raise OSError("Could not acquire lock for token file")
                try:
                    try:
                        tokens = json.load(f)
                        token_info = tokens.get(service)
                        return token_info['data'] if token_info else None
                    except json.JSONDecodeError:
                        logger.error("Token file corrupted")
                        return None
                finally:
                    self._release_lock(f)

    def clear_token(self, service: str) -> bool:
        """Clear token for a specific service"""
        with self.lock:
            if not os.path.exists(self.token_store_path):
                return True

            with open(self.token_store_path, 'r+') as f:
                if not self._acquire_lock(f):
                    raise OSError("Could not acquire lock for token file")
                try:
                    try:
                        tokens = json.load(f)
                        if service in tokens:
                            del tokens[service]
                            f.seek(0)
                            f.truncate()
                            json.dump(tokens, f)
                    except json.JSONDecodeError:
                        logger.error("Token file corrupted")
                        return False
                    return True
                finally:
                    self._release_lock(f)

    def clear_all_tokens(self) -> bool:
        """Clear all tokens"""
        with self.lock:
            try:
                if os.path.exists(self.token_store_path):
                    os.remove(self.token_store_path)
                return True
            except Exception as e:
                logger.error(f"Error clearing tokens: {e}")
                return False

    @property
    def platform(self) -> str:
        """Return the current platform"""
        return "windows" if self.is_windows else "unix"