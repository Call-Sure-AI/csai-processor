# src/services/webrtc/audio_handler.py
from typing import Dict, List, Optional, Any
import logging
import asyncio
from datetime import datetime
import json
import time
import base64
import tempfile
import os
from pathlib import Path

# Import audio processing libraries if needed
# import numpy as np
# import speech_recognition as sr
# import librosa

logger = logging.getLogger(__name__)

class WebRTCAudioHandler:
    """Handles WebRTC audio streams, including receiving, processing, and optionally transcription"""
    
    def __init__(self, audio_save_path: Optional[str] = None):
        self.active_streams: Dict[str, Dict[str, Any]] = {}  # peer_id -> stream info
        self.audio_save_path = audio_save_path
        
        # Create audio save directory if specified
        if audio_save_path:
            os.makedirs(audio_save_path, exist_ok=True)
            logger.info(f"Audio files will be saved to: {audio_save_path}")
        
        # Stats and performance tracking
        self.total_audio_chunks = 0
        self.total_processing_time = 0
        self.active_stream_count = 0
    
    async def start_audio_stream(self, peer_id: str, stream_metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Initialize a new audio stream for a peer"""
        timestamp = datetime.utcnow()
        
        # Create stream record
        stream_info = {
            "peer_id": peer_id,
            "stream_id": stream_metadata.get("stream_id", f"{peer_id}-{int(time.time())}"),
            "started_at": timestamp,
            "last_chunk_at": timestamp,
            "audio_format": stream_metadata.get("format", "webm"),
            "sample_rate": stream_metadata.get("sample_rate", 16000),
            "channels": stream_metadata.get("channels", 1),
            "chunk_count": 0,
            "total_bytes": 0,
            "temporary_files": [],
            "metadata": stream_metadata
        }
        
        # Initialize temp file for audio chunks if saving is enabled
        if self.audio_save_path:
            stream_id = stream_info["stream_id"]
            temp_dir = Path(self.audio_save_path) / "temp" / stream_id
            os.makedirs(temp_dir, exist_ok=True)
            stream_info["temp_dir"] = str(temp_dir)
        
        # Store stream info
        self.active_streams[peer_id] = stream_info
        self.active_stream_count += 1
        
        logger.info(f"Started audio stream for peer {peer_id}: {stream_info['stream_id']}")
        return {
            "status": "started",
            "stream_id": stream_info["stream_id"],
            "timestamp": timestamp.isoformat()
        }
    
    async def process_audio_chunk(self, peer_id: str, chunk_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process an incoming audio chunk from a WebRTC peer"""
        if peer_id not in self.active_streams:
            logger.warning(f"Received audio chunk for inactive stream: {peer_id}")
            return {"status": "error", "error": "Stream not active"}
        
        start_time = time.time()
        stream_info = self.active_streams[peer_id]
        stream_id = stream_info["stream_id"]
        chunk_number = chunk_data.get("chunk_number", stream_info["chunk_count"] + 1)
        
        # Update stream info
        stream_info["last_chunk_at"] = datetime.utcnow()
        stream_info["chunk_count"] += 1
        
        # Extract audio data (base64 encoded)
        audio_base64 = chunk_data.get("audio_data", "")
        if not audio_base64:
            logger.warning(f"Empty audio chunk received for stream {stream_id}")
            return {"status": "error", "error": "Empty audio data"}
        
        try:
            # Decode base64 audio data
            audio_bytes = base64.b64decode(audio_base64)
            stream_info["total_bytes"] += len(audio_bytes)
            
            # Save chunk to temporary file if saving is enabled
            if self.audio_save_path and "temp_dir" in stream_info:
                chunk_filename = f"chunk_{chunk_number:05d}.{stream_info['audio_format']}"
                chunk_path = Path(stream_info["temp_dir"]) / chunk_filename
                
                with open(chunk_path, "wb") as f:
                    f.write(audio_bytes)
                
                stream_info["temporary_files"].append(str(chunk_path))
            
            # Performance tracking
            self.total_audio_chunks += 1
            processing_time = time.time() - start_time
            self.total_processing_time += processing_time
            
            logger.debug(
                f"Processed audio chunk {chunk_number} for stream {stream_id} "
                f"({len(audio_bytes)} bytes, {processing_time:.3f}s)"
            )
            
            return {
                "status": "processed",
                "stream_id": stream_id,
                "chunk_number": chunk_number,
                "processing_time": processing_time
            }
            
        except Exception as e:
            logger.error(f"Error processing audio chunk: {str(e)}")
            return {"status": "error", "error": str(e)}
    
    async def end_audio_stream(self, peer_id: str, final_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """End an active audio stream and perform final processing"""
        if peer_id not in self.active_streams:
            logger.warning(f"Attempt to end inactive stream: {peer_id}")
            return {"status": "error", "error": "Stream not active"}
        
        stream_info = self.active_streams[peer_id]
        stream_id = stream_info["stream_id"]
        
        # Calculate session stats
        duration = (datetime.utcnow() - stream_info["started_at"]).total_seconds()
        chunk_count = stream_info["chunk_count"]
        total_bytes = stream_info["total_bytes"]
        
        # Combine audio chunks if saving is enabled
        result_file = None
        if self.audio_save_path and chunk_count > 0 and "temp_dir" in stream_info:
            try:
                # Create final filename
                timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
                filename = f"{stream_id}_{timestamp}.{stream_info['audio_format']}"
                output_path = Path(self.audio_save_path) / filename
                
                # Here you would implement logic to combine audio chunks
                # This is a placeholder - implementation depends on audio format
                
                result_file = str(output_path)
                logger.info(f"Saved complete audio file to: {result_file}")
                
                # Clean up temporary files
                for temp_file in stream_info["temporary_files"]:
                    try:
                        os.remove(temp_file)
                    except Exception as e:
                        logger.warning(f"Failed to remove temp file {temp_file}: {str(e)}")
                
                # Remove temporary directory
                try:
                    os.rmdir(stream_info["temp_dir"])
                except Exception as e:
                    logger.warning(f"Failed to remove temp dir {stream_info['temp_dir']}: {str(e)}")
                    
            except Exception as e:
                logger.error(f"Error finalizing audio file: {str(e)}")
        
        # Remove stream from active streams
        del self.active_streams[peer_id]
        self.active_stream_count -= 1
        
        logger.info(
            f"Ended audio stream {stream_id} for peer {peer_id}: "
            f"{chunk_count} chunks, {total_bytes} bytes, {duration:.2f}s"
        )
        
        return {
            "status": "completed",
            "stream_id": stream_id,
            "duration": duration,
            "chunk_count": chunk_count,
            "total_bytes": total_bytes,
            "audio_file": result_file
        }
    
    def get_active_stream_info(self, peer_id: Optional[str] = None) -> Dict[str, Any]:
        """Get information about active audio streams"""
        if peer_id:
            if peer_id in self.active_streams:
                return {
                    "stream_info": self.active_streams[peer_id],
                    "is_active": True
                }
            return {"is_active": False}
        
        return {
            "active_streams": len(self.active_streams),
            "streams": {
                peer_id: {
                    "stream_id": info["stream_id"],
                    "started_at": info["started_at"].isoformat(),
                    "last_chunk_at": info["last_chunk_at"].isoformat(),
                    "chunk_count": info["chunk_count"],
                    "total_bytes": info["total_bytes"]
                }
                for peer_id, info in self.active_streams.items()
            }
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get performance statistics for the audio handler"""
        avg_processing_time = 0
        if self.total_audio_chunks > 0:
            avg_processing_time = self.total_processing_time / self.total_audio_chunks
            
        return {
            "active_streams": self.active_stream_count,
            "total_chunks_processed": self.total_audio_chunks,
            "avg_chunk_processing_time": avg_processing_time,
            "total_processing_time": self.total_processing_time
        }