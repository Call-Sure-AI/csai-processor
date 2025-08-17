"""
WebRTC Manager for CSAI Processor
Optimized version migrated from cs_ai_backend
"""
import logging
import json
import asyncio
import time
from typing import Dict, Any, Optional, List
from datetime import datetime
from fastapi import WebSocket
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class WebRTCPeer:
    """Represents a WebRTC peer connection"""
    
    def __init__(self, peer_id: str, company_info: Dict[str, Any], websocket: WebSocket):
        self.peer_id = peer_id
        self.company_info = company_info
        self.websocket = websocket
        self.connected_at = time.time()
        self.last_activity = time.time()
        self.message_count = 0
        self.is_connected = True
        
    async def send_message(self, message: Dict[str, Any]):
        """Send message to peer"""
        try:
            if self.is_connected:
                await self.websocket.send_json(message)
                self.last_activity = time.time()
                self.message_count += 1
                return True
        except Exception as e:
            logger.error(f"Error sending message to peer {self.peer_id}: {str(e)}")
            self.is_connected = False
        return False
    
    def update_activity(self):
        """Update last activity timestamp"""
        self.last_activity = time.time()
    
    def disconnect(self):
        """Mark peer as disconnected"""
        self.is_connected = False


class WebRTCManager:
    """Manages WebRTC connections and signaling"""
    
    def __init__(self):
        self.peers: Dict[str, WebRTCPeer] = {}
        self.company_peers: Dict[str, List[str]] = {}
        self.connection_manager = None
        self.speech_services = {}
        self.audio_handler = {}
        self.stats = {
            "total_connections": 0,
            "active_connections": 0,
            "messages_relayed": 0,
            "errors": 0
        }
        
    def initialize_services(self, db: Session, vector_store):
        """Initialize WebRTC manager with required services"""
        try:
            # Store references to services
            self.db = db
            self.vector_store = vector_store
            logger.info("WebRTC manager services initialized")
        except Exception as e:
            logger.error(f"Error initializing WebRTC manager services: {str(e)}")
    
    async def register_peer(self, peer_id: str, company_info: Dict[str, Any], websocket: WebSocket) -> WebRTCPeer:
        """Register a new peer connection"""
        try:
            # Create peer instance
            peer = WebRTCPeer(peer_id, company_info, websocket)
            
            # Store peer
            self.peers[peer_id] = peer
            
            # Add to company peers
            company_id = str(company_info.get('id', 'unknown'))
            if company_id not in self.company_peers:
                self.company_peers[company_id] = []
            self.company_peers[company_id].append(peer_id)
            
            # Update stats
            self.stats["total_connections"] += 1
            self.stats["active_connections"] = len([p for p in self.peers.values() if p.is_connected])
            
            logger.info(f"Registered peer {peer_id} for company {company_id}")
            return peer
            
        except Exception as e:
            logger.error(f"Error registering peer {peer_id}: {str(e)}")
            self.stats["errors"] += 1
            raise
    
    async def unregister_peer(self, peer_id: str):
        """Unregister a peer connection"""
        try:
            peer = self.peers.get(peer_id)
            if peer:
                # Remove from company peers
                company_id = str(peer.company_info.get('id', 'unknown'))
                if company_id in self.company_peers:
                    self.company_peers[company_id] = [
                        p for p in self.company_peers[company_id] if p != peer_id
                    ]
                
                # Clean up speech service
                if peer_id in self.speech_services:
                    speech_service = self.speech_services.pop(peer_id)
                    if hasattr(speech_service, 'close_session'):
                        await speech_service.close_session(peer_id)
                
                # Remove peer
                del self.peers[peer_id]
                
                # Update stats
                self.stats["active_connections"] = len([p for p in self.peers.values() if p.is_connected])
                
                logger.info(f"Unregistered peer {peer_id}")
                
        except Exception as e:
            logger.error(f"Error unregistering peer {peer_id}: {str(e)}")
            self.stats["errors"] += 1
    
    async def relay_signal(self, from_peer_id: str, to_peer_id: str, signal_data: Dict[str, Any]) -> bool:
        """Relay WebRTC signaling message between peers"""
        try:
            # Special handling for signals sent to the server
            if to_peer_id == "server":
                logger.info(f"Processing server-bound signal from {from_peer_id}")
                
                # Get the sender peer
                if from_peer_id in self.peers:
                    from_peer = self.peers[from_peer_id]
                    
                    # Handle offer signal
                    if signal_data.get('type') == 'offer':
                        logger.info(f"Received offer from {from_peer_id}, sending answer")
                        
                        # Create a minimal answer to acknowledge the connection
                        answer = {
                            'type': 'signal',
                            'from_peer': 'server',
                            'data': {
                                'type': 'answer',
                                'sdp': {
                                    'type': 'answer',
                                    'sdp': 'v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=msid-semantic: WMS\r\n'
                                }
                            }
                        }
                        
                        # Send the answer back to the client
                        await from_peer.send_message(answer)
                        
                        # Also send a connection success message
                        await from_peer.send_message({
                            'type': 'connection_success',
                            'message': 'WebRTC signaling completed successfully'
                        })
                        
                        logger.info(f"Sent WebRTC answer to {from_peer_id}")
                        self.stats["messages_relayed"] += 1
                        return True
            
            # Relay to specific peer
            elif to_peer_id in self.peers:
                target_peer = self.peers[to_peer_id]
                message = {
                    'type': 'signal',
                    'from_peer': from_peer_id,
                    'data': signal_data
                }
                
                success = await target_peer.send_message(message)
                if success:
                    self.stats["messages_relayed"] += 1
                    return True
                else:
                    logger.warning(f"Failed to relay signal to {to_peer_id}")
                    return False
            else:
                logger.warning(f"Target peer {to_peer_id} not found")
                return False
                
        except Exception as e:
            logger.error(f"Error relaying signal from {from_peer_id} to {to_peer_id}: {str(e)}")
            self.stats["errors"] += 1
            return False
    
    def get_peer(self, peer_id: str) -> Optional[WebRTCPeer]:
        """Get peer by ID"""
        return self.peers.get(peer_id)
    
    def get_company_peers(self, company_id: str) -> List[str]:
        """Get all peers for a company"""
        return self.company_peers.get(str(company_id), [])
    
    def get_all_peers(self) -> List[str]:
        """Get all peer IDs"""
        return list(self.peers.keys())
    
    def get_active_peers(self) -> List[str]:
        """Get all active peer IDs"""
        return [peer_id for peer_id, peer in self.peers.items() if peer.is_connected]
    
    async def broadcast_to_company(self, company_id: str, message: Dict[str, Any]) -> int:
        """Broadcast message to all peers in a company"""
        try:
            company_peer_ids = self.get_company_peers(company_id)
            sent_count = 0
            
            for peer_id in company_peer_ids:
                peer = self.get_peer(peer_id)
                if peer and peer.is_connected:
                    if await peer.send_message(message):
                        sent_count += 1
            
            logger.info(f"Broadcasted message to {sent_count}/{len(company_peer_ids)} peers in company {company_id}")
            return sent_count
            
        except Exception as e:
            logger.error(f"Error broadcasting to company {company_id}: {str(e)}")
            self.stats["errors"] += 1
            return 0
    
    async def send_to_peer(self, peer_id: str, message: Dict[str, Any]) -> bool:
        """Send message to specific peer"""
        try:
            peer = self.get_peer(peer_id)
            if peer and peer.is_connected:
                success = await peer.send_message(message)
                if success:
                    self.stats["messages_relayed"] += 1
                return success
            else:
                logger.warning(f"Peer {peer_id} not found or not connected")
                return False
                
        except Exception as e:
            logger.error(f"Error sending message to peer {peer_id}: {str(e)}")
            self.stats["errors"] += 1
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """Get WebRTC manager statistics"""
        try:
            # Calculate additional stats
            company_stats = {}
            for company_id, peer_ids in self.company_peers.items():
                active_peers = [pid for pid in peer_ids if self.get_peer(pid) and self.get_peer(pid).is_connected]
                company_stats[company_id] = {
                    "total_peers": len(peer_ids),
                    "active_peers": len(active_peers)
                }
            
            # Get peer details
            peer_details = []
            for peer_id, peer in self.peers.items():
                peer_details.append({
                    "peer_id": peer_id,
                    "company_id": str(peer.company_info.get('id', 'unknown')),
                    "connected_at": datetime.fromtimestamp(peer.connected_at).isoformat(),
                    "last_activity": datetime.fromtimestamp(peer.last_activity).isoformat(),
                    "message_count": peer.message_count,
                    "is_connected": peer.is_connected
                })
            
            return {
                "total_peers": len(self.peers),
                "active_peers": len(self.get_active_peers()),
                "total_companies": len(self.company_peers),
                "company_stats": company_stats,
                "peer_details": peer_details,
                "manager_stats": self.stats,
                "speech_services": len(self.speech_services),
                "timestamp": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error getting WebRTC stats: {str(e)}")
            return {"error": str(e)}
    
    async def cleanup_inactive_peers(self, timeout_seconds: int = 300):
        """Clean up inactive peer connections"""
        try:
            current_time = time.time()
            inactive_peers = []
            
            for peer_id, peer in self.peers.items():
                if current_time - peer.last_activity > timeout_seconds:
                    inactive_peers.append(peer_id)
            
            for peer_id in inactive_peers:
                logger.info(f"Cleaning up inactive peer {peer_id}")
                await self.unregister_peer(peer_id)
            
            if inactive_peers:
                logger.info(f"Cleaned up {len(inactive_peers)} inactive peers")
                
        except Exception as e:
            logger.error(f"Error cleaning up inactive peers: {str(e)}")
            self.stats["errors"] += 1
    
    async def close_all_connections(self):
        """Close all WebRTC connections"""
        try:
            logger.info(f"Closing all WebRTC connections ({len(self.peers)} peers)")
            
            # Close all peer connections
            for peer_id in list(self.peers.keys()):
                await self.unregister_peer(peer_id)
            
            # Clear all data
            self.peers.clear()
            self.company_peers.clear()
            self.speech_services.clear()
            self.audio_handler.clear()
            
            logger.info("All WebRTC connections closed")
            
        except Exception as e:
            logger.error(f"Error closing WebRTC connections: {str(e)}")
            self.stats["errors"] += 1
    
    def health_check(self) -> Dict[str, Any]:
        """Perform health check on WebRTC manager"""
        try:
            return {
                "status": "healthy",
                "active_connections": len(self.get_active_peers()),
                "total_peers": len(self.peers),
                "errors": self.stats["errors"],
                "timestamp": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"Error in WebRTC health check: {str(e)}")
            return {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
