# src/services/webrtc/peer_connection.py
from typing import Optional, Dict, Any
import logging
from datetime import datetime
import asyncio
import json


logger = logging.getLogger(__name__)

class PeerConnection:
    def __init__(self, peer_id: str, company_info: dict):
        self.peer_id = peer_id
        self.company_id = str(company_info['id'])
        self.company_info = company_info
        self.connected_at = datetime.utcnow()
        self.last_activity = self.connected_at
        self.websocket = None
        self.message_count = 0
        self.is_closed = False
        
    async def set_websocket(self, websocket) -> bool:
        """Set the WebSocket connection and update status flags"""
        try:
            self.websocket = websocket
            self.websocket_connected = True
            self.last_activity = datetime.utcnow()
            
            # Save a custom flag to prevent premature closure detection
            setattr(self.websocket, '_peer_connected', True)
            
            logger.info(f"WebSocket set for peer {self.peer_id}")
            return True
        except Exception as e:
            logger.error(f"Error setting WebSocket: {str(e)}")
            return False
        
    async def send_message(self, message: dict) -> bool:
        """Send message to peer with improved error handling"""
        try:
            if not hasattr(self, 'websocket') or self.websocket is None:
                logger.error(f"Cannot send message to peer {self.peer_id}: No WebSocket connection")
                return False
                
            # Try to use WebSocket's built-in send_json if available
            if hasattr(self.websocket, 'send_json'):
                await asyncio.wait_for(
                    self.websocket.send_json(message),
                    timeout=5.0
                )
            else:
                # Otherwise, manually serialize to JSON
                message_str = json.dumps(message)
                await asyncio.wait_for(
                    self.websocket.send_text(message_str),
                    timeout=5.0
                )
            
            # Update last activity timestamp
            self.last_activity = datetime.utcnow()
            
            # Log important message types
            if message.get('type') in ['config', 'signal', 'connection_success']:
                logger.info(f"Sent {message.get('type')} message to {self.peer_id}")
                
            return True
            
        except Exception as e:
            logger.error(f"Error sending message to peer {self.peer_id}: {str(e)}")
            return False
    
        
    async def close(self):
        """Close peer connection with proper error handling"""
        try:
            if hasattr(self, 'websocket') and self.websocket is not None:
                # Mark as no longer connected before actual close
                self.websocket_connected = False
                
                # Try to send a closure message first
                try:
                    await self.send_message({
                        "type": "connection_closed",
                        "message": "Peer connection closed by server"
                    })
                except Exception:
                    # Ignore errors in closure message
                    pass
                    
                # Close the WebSocket if not already closed
                if not hasattr(self.websocket, "_closed") or not self.websocket._closed:
                    try:
                        await self.websocket.close()
                    except Exception as e:
                        logger.error(f"Error closing WebSocket for peer {self.peer_id}: {str(e)}")
                        
            # Clear references
            self.websocket = None
            
        except Exception as e:
            logger.error(f"Error closing peer connection: {str(e)}")
    
           
    def is_active(self, timeout_seconds: int = 300) -> bool:
        """Check if the peer connection is active within timeout period"""
        if self.is_closed or not self.websocket:
            return False
        
        try:
            # Check FastAPI WebSocket client_state and application_state
            if hasattr(self.websocket, 'client_state') and self.websocket.client_state.name == "DISCONNECTED":
                return False
            if hasattr(self.websocket, 'application_state') and self.websocket.application_state.name == "DISCONNECTED":
                return False
        except Exception:
            # If attributes don't exist or we get an error, fall back to time-based check
            pass
            
        time_diff = (datetime.utcnow() - self.last_activity).total_seconds()
        return time_diff < timeout_seconds

    def get_stats(self) -> Dict[str, Any]:
        """Get connection statistics"""
        return {
            "peer_id": self.peer_id,
            "company_id": self.company_id,
            "connected_at": self.connected_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "message_count": self.message_count,
            "is_connected": bool(self.websocket and not self.is_closed)
        }