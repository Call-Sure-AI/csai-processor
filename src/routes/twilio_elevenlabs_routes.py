from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session
from database.config import SessionLocal
from database.models import CallType, ConversationTurn
from services.speech.deepgram_ws_service import DeepgramWebSocketService
from services.voice.elevenlabs_service import elevenlabs_service
from services.rag.rag_service import get_rag_service
from twilio.twiml.voice_response import VoiceResponse, Connect
from datetime import datetime, timedelta
from config.settings import settings
from database.config import get_db
import logging
import json
import asyncio
from services.intent_router_service import intent_router_service
from services.agent_config_service import agent_config_service
from services.prompt_template_service import prompt_template_service
from services.call_recording_service import call_recording_service
from database.models import ConversationTurn, Call
from pydantic import BaseModel, Field
from twilio.rest import Client
from urllib.parse import quote
from services.agent_tools import execute_function
from services.intent_detection_service import intent_detection_service
import uuid

twilio_client = Client(settings.twilio_account_sid, settings.twilio_auth_token)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/twilio-elevenlabs", tags=["twilio-elevenlabs"])

# Active call context
call_context = {}


@router.post("/incoming-call")
async def handle_incoming_call_elevenlabs(request: Request):
    """Incoming call handler with ElevenLabs via WebSocket"""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        from_number = form_data.get("From")
        to_number = form_data.get("To")
        
        global from_number_global
        global to_number_global

        from_number_global = from_number
        to_number_global = to_number

        company_id = request.query_params.get("company_id")
        agent_id = request.query_params.get("agent_id")
        
        logger.info(f"INCOMING CALL - CallSid: {call_sid}")
        logger.info(f"From: {from_number}, To: {to_number}")
        logger.info(f"Company: {company_id}, Agent: {agent_id}")
        
        # Generate TwiML response
        response = VoiceResponse()
        response.pause(length=1)
        
        # Connect to Media Stream
        connect = Connect()
        ws_domain = settings.base_url.replace('https://', '').replace('http://', '')

        call_context[call_sid] = {
            "company_id": company_id,
            "agent_id": agent_id,
            "from_number": from_number,
            "to_number": to_number
        }
        
        stream_url = f"wss://{ws_domain}/api/v1/twilio-elevenlabs/media-stream?call_sid={call_sid}"
        
        logger.info(f"Stream URL: {stream_url}")
        
        connect.stream(url=stream_url)
        response.append(connect)
        
        twiml_str = str(response)
        logger.info(f"TwiML Response:\n{twiml_str}")
        
        return Response(content=twiml_str, media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        response = VoiceResponse()
        response.say("An error occurred. Please try again.", voice="Polly.Joanna")
        return Response(content=str(response), media_type="application/xml")

@router.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Incoming call handler with proper echo prevention and interruption handling"""
    try:
        logger.info("WebSocket connection attempt...")
        await websocket.accept()
        logger.info("WebSocket ACCEPTED")
    except Exception as e:
        logger.error(f"Failed to accept WebSocket: {str(e)}")
        return
    
    conversation_transcript = []
    call_sid = websocket.query_params.get("call_sid")
    first_message_data = None
    is_agent_speaking = False
    stop_audio_flag = {'stop': False}
    current_audio_task = None
    current_processing_task = None
    greeting_sent = False
    greeting_start_time = None
    
    logger.info(f"Query params: {dict(websocket.query_params)}")
    
    if not call_sid:
        logger.info("No call_sid in query, waiting for Twilio 'start' event...")
        try:
            for attempt in range(3):
                message = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                data = json.loads(message)
                event_type = data.get("event")
                logger.info(f"Received event #{attempt + 1}: {event_type}")
                
                if event_type == "connected":
                    logger.info("Received 'connected' event")
                    continue
                elif event_type == "start":
                    call_sid = data.get("start", {}).get("callSid")
                    first_message_data = data
                    logger.info(f"Extracted call_sid: {call_sid}")
                    break
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for start event")
            await websocket.close(code=1008)
            return
        except Exception as e:
            logger.error(f"Error receiving messages: {str(e)}")
            await websocket.close(code=1011)
            return
    
    if not call_sid:
        logger.error("Could not obtain call_sid")
        await websocket.close(code=1008)
        return
    
    logger.info(f"Call SID validated: {call_sid}")
    
    # Get context from stored data
    context = call_context.get(call_sid, {})
    company_id = context.get("company_id")
    master_agent_id = context.get("agent_id")
    
    logger.info(f"Company ID: {company_id}, Master Agent: {master_agent_id}")
    
    master_agent = await agent_config_service.get_master_agent(company_id, master_agent_id)
    current_agent_context = master_agent
    
    call_metadata = {
        'start_time': datetime.utcnow(),
        'from_number': from_number_global,
        'to_number': to_number_global,
        'company_id': company_id
    }
    
    logger.info(f"Detected Company Id {company_id} for storing in Call Database")
    
    if not master_agent:
        logger.error(f"Master agent {master_agent_id} not found!")
        await websocket.close(code=1008, reason="Master agent not found")
        return
    
    logger.info(f"Master: {master_agent['name']}")
    
    available_agents = await agent_config_service.get_company_agents(company_id)
    specialized_agents = [
        a for a in available_agents
        if a['agent_id'] != master_agent_id
    ]
    
    logger.info(f"Specialized agents: {[a['name'] for a in specialized_agents]}")
    
    # Initialize services
    db = SessionLocal()
    deepgram_service = DeepgramWebSocketService()
    rag = get_rag_service()
    stream_sid = None
    
    call_state = {
        "first_interaction": True,
        "interaction_count": 0
    }
    
    try:
        async def on_deepgram_transcript(session_id: str, transcript: str):
            nonlocal conversation_transcript
            nonlocal is_agent_speaking
            nonlocal current_agent_context
            nonlocal current_audio_task
            nonlocal current_processing_task
            nonlocal stop_audio_flag
            nonlocal greeting_start_time
            
            if not transcript.strip():
                return
            
            if greeting_start_time:
                elapsed = (datetime.utcnow() - greeting_start_time).total_seconds()
                if elapsed < 3:
                    logger.debug(f"Ignoring early transcript (echo): '{transcript}' at {elapsed:.1f}s")
                    return
            
            logger.info(f"📝 Transcript: '{transcript}' | Agent speaking: {is_agent_speaking}")
            
            if is_agent_speaking:
                word_count = len(transcript.split())
                if word_count >= 3:
                    logger.warning(f"⚠️ REAL INTERRUPTION: '{transcript}'")
                    
                    # Set stop flag
                    stop_audio_flag['stop'] = True
                    
                    # Cancel BOTH audio playback AND processing tasks
                    if current_audio_task and not current_audio_task.done():
                        logger.info("Cancelling audio playback...")
                        current_audio_task.cancel()
                        try:
                            await current_audio_task
                        except asyncio.CancelledError:
                            pass
                    
                    if current_processing_task and not current_processing_task.done():
                        logger.info("Cancelling RAG processing...")
                        current_processing_task.cancel()
                        try:
                            await current_processing_task
                        except asyncio.CancelledError:
                            pass
                    
                    # Mark agent as not speaking
                    is_agent_speaking = False
                    
                    # Wait for buffer clear
                    logger.info("Waiting for Twilio to clear...")
                    await asyncio.sleep(1.0)
                    
                    # Reset flag
                    stop_audio_flag['stop'] = False
                    logger.info("✓ Ready for new input")
                else:
                    logger.debug(f"Ignoring short utterance: '{transcript}' ({word_count} words)")
                    return
            
            logger.info(f"USER SAID: '{transcript}'")
            
            # Save to transcript
            conversation_transcript.append({
                'role': 'user',
                'content': transcript,
                'timestamp': datetime.utcnow().isoformat()
            })
            
            # Sentiment analysis
            sentiment_analysis = prompt_template_service.detect_sentiment_and_urgency(
                transcript,
                current_agent_context
            )
            
            logger.info(f"Sentiment: {sentiment_analysis['sentiment']}, Urgency: {sentiment_analysis['urgency']}")
            
            if sentiment_analysis['urgency_keywords']:
                logger.warning(f"HIGH URGENCY DETECTED: {sentiment_analysis['urgency_keywords']}")
            
            # Save to DB
            try:
                db.add(ConversationTurn(
                    call_sid=call_sid,
                    role="user",
                    content=transcript,
                    created_at=datetime.utcnow()
                ))
                db.commit()
            except Exception as e:
                logger.error(f"DB error: {e}")
            
            # Handle urgent requests
            urgent_acknowledgment = None
            if sentiment_analysis['urgency'] == 'high' and sentiment_analysis['suggested_action']:
                urgent_acknowledgment = sentiment_analysis['suggested_action']
                logger.info(f"URGENT RESPONSE: '{urgent_acknowledgment}'")
                
                is_agent_speaking = True
                stop_audio_flag['stop'] = False
                
                current_audio_task = asyncio.create_task(
                    stream_elevenlabs_audio_with_playback(websocket, stream_sid, urgent_acknowledgment, stop_audio_flag)
                )
                
                try:
                    await current_audio_task
                except asyncio.CancelledError:
                    logger.info("Urgent acknowledgment cancelled")
                finally:
                    is_agent_speaking = False
                    current_audio_task = None
            
            # Agent routing
            current_agent_id = master_agent_id
            
            if specialized_agents:
                logger.info(f"Detecting intent for message #{call_state['interaction_count'] + 1}...")
                
                detected_agent = await intent_router_service.detect_intent(
                    transcript,
                    company_id,
                    master_agent,
                    specialized_agents
                )
                
                if detected_agent:
                    previous_agent_id = intent_router_service.get_current_agent(call_sid, master_agent_id)
                    intent_router_service.set_current_agent(call_sid, detected_agent)
                    current_agent_id = detected_agent
                    
                    agent_info = await agent_config_service.get_agent_by_id(detected_agent)
                    
                    if agent_info:
                        current_agent_context = agent_info
                        
                        if detected_agent != previous_agent_id and call_state["interaction_count"] > 0:
                            logger.info(f"RE-ROUTING to {agent_info['name']}")
                            
                            routing_message = f"Let me connect you with our {agent_info['name']}."
                            is_agent_speaking = True
                            stop_audio_flag['stop'] = False
                            
                            current_audio_task = asyncio.create_task(
                                stream_elevenlabs_audio_with_playback(websocket, stream_sid, routing_message, stop_audio_flag)
                            )
                            
                            await asyncio.sleep(0.5)
                            
                            try:
                                await current_audio_task
                            except asyncio.CancelledError:
                                logger.info("Routing message cancelled")
                            finally:
                                is_agent_speaking = False
                                current_audio_task = None
                        else:
                            logger.info(f"ROUTED to {agent_info['name']}")
                else:
                    logger.info(f"   Staying with MASTER")
            
            call_state["interaction_count"] += 1
            
            # Process and respond
            is_agent_speaking = True
            await asyncio.sleep(0.3)
            stop_audio_flag['stop'] = False
            
            current_processing_task = asyncio.create_task(
                process_and_respond_incoming(
                    transcript=transcript,
                    websocket=websocket,
                    stream_sid=stream_sid,
                    stop_audio_flag=stop_audio_flag,
                    db=db,
                    call_sid=call_sid,
                    current_agent_context=current_agent_context,
                    current_agent_id=current_agent_id,
                    company_id=company_id,
                    conversation_transcript=conversation_transcript,
                    sentiment_analysis=sentiment_analysis,
                    urgent_acknowledgment=urgent_acknowledgment,
                    call_metadata=call_metadata
                )
            )
            
            try:
                await current_processing_task
                logger.info("✓ Response completed")
            except asyncio.CancelledError:
                logger.info("Response cancelled by interruption")
            finally:
                is_agent_speaking = False
                current_audio_task = None
                current_processing_task = None
        
        # Initialize Deepgram
        session_id = f"deepgram_{call_sid}"
        logger.info(f"Initializing Deepgram...")
        
        deepgram_connected = await deepgram_service.initialize_session(
            session_id=session_id,
            callback=on_deepgram_transcript
        )
        
        if not deepgram_connected:
            logger.error(f"Deepgram connection failed")
            await websocket.close()
            return
        
        logger.info(f"Deepgram connected")
        
        if first_message_data and first_message_data.get("event") == "start":
            logger.info("Processing buffered start event...")
            stream_sid = first_message_data.get("streamSid")
            logger.info(f"STREAM STARTED: {stream_sid}")
            
            greeting = prompt_template_service.generate_greeting(master_agent)
            logger.info(f"Sending greeting: '{greeting}'")
            
            is_agent_speaking = True
            stop_audio_flag['stop'] = False
            greeting_start_time = datetime.utcnow()
            
            current_audio_task = asyncio.create_task(
                stream_elevenlabs_audio_with_playback(websocket, stream_sid, greeting, stop_audio_flag)
            )
            
            try:
                await current_audio_task
                logger.info("✓ Greeting completed - ready for customer response")
            except asyncio.CancelledError:
                logger.info("Greeting cancelled")
            finally:
                is_agent_speaking = False
                current_audio_task = None
            
            greeting_sent = True
            await asyncio.sleep(0.5)
        
        logger.info("Entering message loop...")
        
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                data = json.loads(message)
                event = data.get("event")
                
                if event == "connected":
                    logger.debug("Connected event")
                
                elif event == "start":
                    if not greeting_sent:
                        stream_sid = data.get("streamSid")
                        logger.info(f"STREAM STARTED: {stream_sid}")
                        
                        greeting = prompt_template_service.generate_greeting(master_agent)
                        logger.info(f"Sending greeting: '{greeting}'")
                        
                        is_agent_speaking = True
                        stop_audio_flag['stop'] = False
                        greeting_start_time = datetime.utcnow()
                        
                        current_audio_task = asyncio.create_task(
                            stream_elevenlabs_audio_with_playback(websocket, stream_sid, greeting, stop_audio_flag)
                        )
                        
                        try:
                            await current_audio_task
                            logger.info("Greeting completed")
                        except asyncio.CancelledError:
                            pass
                        finally:
                            is_agent_speaking = False
                            current_audio_task = None
                        
                        greeting_sent = True
                        await asyncio.sleep(0.5)
                
                elif event == "media":
                    # CRITICAL: Always process user audio for real-time interruption
                    payload = data.get("media", {}).get("payload")
                    if payload:
                        audio = await deepgram_service.convert_twilio_audio(payload, session_id)
                        if audio:
                            await deepgram_service.process_audio_chunk(session_id, audio)
                
                elif event == "stop":
                    logger.info(f"STREAM STOPPED")
                    break
            
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected")
                break
            except Exception as e:
                logger.error(f"Error: {str(e)}")
                break
    
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
    
    finally:
        logger.info(f"Cleaning up {call_sid}")
        logger.info(f"From: {call_metadata.get('from_number')}")
        logger.info(f"To: {call_metadata.get('to_number')}")
        logger.info(f"Company ID: {call_metadata.get('company_id')}")
        
        try:
            call_duration = 0
            if call_metadata.get('start_time'):
                call_duration = int((datetime.utcnow() - call_metadata['start_time']).total_seconds())
            
            logger.info(f"Uploading call data to S3...")
            logger.info(f"Duration: {call_duration}s")
            logger.info(f"Transcript turns: {len(conversation_transcript)}")
            
            s3_urls = await call_recording_service.save_call_data(
                call_sid=call_sid,
                company_id=company_id,
                agent_id=master_agent_id,
                transcript=conversation_transcript,
                recording_url=None,
                duration=call_duration,
                from_number=call_metadata.get('from_number'),
                to_number=call_metadata.get('to_number')
            )
            
            logger.info(f"S3 Upload Complete:")
            logger.info(f"Transcript: {s3_urls.get('transcript_url')}")
            
            try:
                call_record = db.query(Call).filter_by(call_sid=call_sid).first()
                
                if call_record:
                    call_record.transcription = s3_urls.get('transcript_url')
                    call_record.company_id = s3_urls.get('company_id')
                    call_record.from_number = s3_urls.get('from_number')
                    call_record.to_number = s3_urls.get('to_number')
                    call_record.duration = call_duration
                    call_record.status = 'completed'
                    call_record.ended_at = datetime.utcnow()
                    db.commit()
                    logger.info(f"Database updated with S3 URLs")
                else:
                    new_call = Call(
                        call_sid=call_sid,
                        company_id=call_metadata.get('company_id'),
                        from_number=call_metadata.get('from_number'),
                        to_number=call_metadata.get('to_number'),
                        status='completed',
                        duration=call_duration,
                        transcription=s3_urls.get('transcript_url'),
                        created_at=call_metadata['start_time'],
                        ended_at=datetime.utcnow()
                    )
                    db.add(new_call)
                    db.commit()
                    logger.info(f"Call record created")
            except Exception as db_error:
                logger.error(f"Database update error: {db_error}")
                db.rollback()
        
        except Exception as upload_error:
            logger.error(f"S3 upload error: {upload_error}")
            import traceback
            logger.error(traceback.format_exc())
        
        intent_router_service.clear_call(call_sid)
        
        try:
            await deepgram_service.close_session(session_id)
        except:
            pass
        
        call_context.pop(call_sid, None)
        db.close()
        
        logger.info(f"Cleanup complete")


async def stream_elevenlabs_audio_with_playback(websocket: WebSocket, stream_sid: str, text: str, stop_flag_ref: dict):
    """Stream audio AND simulate playback in one task for proper cancellation"""
    if not stream_sid:
        logger.error("No stream_sid")
        return
    
    try:
        logger.info(f"🔊 Generating audio: '{text[:50]}...'")
        chunk_count = 0
        
        # Stream audio chunks
        async for audio_chunk in elevenlabs_service.generate(text):
            if stop_flag_ref.get('stop', False):
                logger.info(f"🛑 Audio interrupted after {chunk_count} chunks")
                try:
                    clear_message = {"event": "clear", "streamSid": stream_sid}
                    await websocket.send_json(clear_message)
                    logger.info("✓ Sent clear command")
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.error(f"Clear error: {e}")
                raise asyncio.CancelledError()
            
            if audio_chunk and stream_sid:
                message = {
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": audio_chunk}
                }
                await websocket.send_json(message)
                chunk_count += 1
        
        logger.info(f"✓ Sent {chunk_count} chunks to Twilio")
        
        # Calculate playback duration (each chunk ~20ms + buffer)
        playback_duration = (chunk_count * 0.02) + 0.5
        logger.info(f"⏳ Simulating playback: {playback_duration:.1f}s")
        
        # Wait for playback while checking for interruption
        elapsed = 0
        check_interval = 0.1
        
        while elapsed < playback_duration:
            if stop_flag_ref.get('stop', False):
                logger.info(f"🛑 Playback interrupted at {elapsed:.1f}s")
                try:
                    clear_message = {"event": "clear", "streamSid": stream_sid}
                    await websocket.send_json(clear_message)
                    await asyncio.sleep(0.5)
                except:
                    pass
                raise asyncio.CancelledError()
            
            await asyncio.sleep(check_interval)
            elapsed += check_interval
        
        logger.info(f"✓ Playback completed: {playback_duration:.1f}s")
        
    except asyncio.CancelledError:
        logger.info(f"🛑 Audio+playback cancelled")
        try:
            clear_message = {"event": "clear", "streamSid": stream_sid}
            await websocket.send_json(clear_message)
            await asyncio.sleep(0.5)
        except:
            pass
        raise
    except Exception as e:
        logger.error(f"Error streaming audio: {e}")

def should_use_rag(transcript: str, conversation_history: list, intent_analysis: dict = None) -> dict:
    transcript_lower = transcript.lower().strip()
    words = transcript_lower.split()
    word_count = len(words)
    
    buying_readiness = 0
    if intent_analysis:
        buying_readiness = intent_analysis.get('buying_readiness', 0)
    
    is_active_conversation = buying_readiness > 40
    
    incomplete_indicators = ['then', 'so', 'but', 'and', 'or', 'because', 'well', 'um', 'uh']
    last_word = words[-1] if words else ''
    if last_word in incomplete_indicators and word_count <= 3:
        return {
            'use_rag': True,
            'direct_response': None,
            'reason': 'incomplete_sentence'
        }

    simple_acknowledgments = [
        'ok', 'okay', 'thanks', 'thank you', 'alright', 'sure', 
        'yes', 'no', 'yeah', 'yep', 'nope', 'got it', 'understood',
        'i see', 'makes sense', 'cool', 'great', 'perfect', 'fine'
    ]

    if word_count <= 2 and transcript_lower in simple_acknowledgments:
        if is_active_conversation:
            return {
                'use_rag': True,
                'direct_response': None,
                'reason': 'active_conversation_acknowledgment'
            }
        else:
            responses = [
                "Is there anything else I can help you with?",
                "What else can I assist you with today?",
                "Do you have any other questions?"
            ]
            import random
            return {
                'use_rag': False,
                'direct_response': random.choice(responses),
                'reason': 'simple_acknowledgment'
            }

    if transcript_lower in ['thanks', 'thank you', 'thanks bye', 'thank you bye']:
        return {
            'use_rag': False,
            'direct_response': "You're welcome! Have a great day!",
            'reason': 'gratitude_farewell'
        }

    booking_keywords = ['book', 'schedule', 'appointment', 'slot', 'available', 'reserve', 'confirm booking']
    if any(keyword in transcript_lower for keyword in booking_keywords):
        info_keywords = ['when', 'what time', 'how', 'can i', 'do you', 'is it possible']
        if any(kw in transcript_lower for kw in info_keywords):
            return {'use_rag': True, 'direct_response': None, 'reason': 'booking_inquiry'}
        else:
            return {
                'use_rag': False,
                'direct_response': None,
                'reason': 'booking_action'
            }

    greetings = ['hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening']
    if any(greeting in transcript_lower for greeting in greetings) and word_count <= 4:
        if len(conversation_history) > 2:
            return {'use_rag': True, 'direct_response': None, 'reason': 'mid_conversation'}
        return {
            'use_rag': False,
            'direct_response': "Hello! How can I assist you today?",
            'reason': 'greeting'
        }

    farewells = ['bye', 'goodbye', 'see you', 'talk later', 'have a good', 'that\'s all', 'i\'m done', 'all done']
    if any(farewell in transcript_lower for farewell in farewells):
        return {
            'use_rag': False,
            'direct_response': "Thank you for calling. Have a great day!",
            'reason': 'farewell'
        }

    return {'use_rag': True, 'direct_response': None, 'reason': 'complex_query'}


async def process_and_respond_incoming(
    transcript: str,
    websocket: WebSocket,
    stream_sid: str,
    stop_audio_flag: dict,
    db: Session,
    call_sid: str,
    current_agent_context: dict,
    current_agent_id: str,
    company_id: str,
    conversation_transcript: list,
    sentiment_analysis: dict,
    urgent_acknowledgment: str = None,
    call_metadata: dict = None
):
    """Process incoming call and respond - fully cancellable"""
    
    rag = get_rag_service()
    
    try:
        # Determine if we should use RAG
        rag_decision = should_use_rag(transcript, conversation_transcript, None)
        
        logger.info(f"RAG Decision: {rag_decision['reason']} - Use RAG: {rag_decision['use_rag']}")
        
        # If direct response available, use it
        if not rag_decision['use_rag'] and rag_decision['direct_response']:
            logger.info(f"Using direct response: '{rag_decision['direct_response']}'")
            
            conversation_transcript.append({
                'role': 'assistant',
                'content': rag_decision['direct_response'],
                'timestamp': datetime.utcnow().isoformat(),
                'rag_used': False
            })
            
            db.add(ConversationTurn(
                call_sid=call_sid,
                role="assistant",
                content=rag_decision['direct_response'],
                created_at=datetime.utcnow()
            ))
            db.commit()
            
            await stream_elevenlabs_audio_with_playback(
                websocket, stream_sid, rag_decision['direct_response'], stop_audio_flag
            )
            return
        
        # Build conversation context
        conversation_messages = []
        for msg in conversation_transcript[-10:]:
            if msg['role'] in ['user', 'assistant']:
                conversation_messages.append({
                    'role': msg['role'],
                    'content': msg['content']
                })
        
        # Single intelligent prompt for incoming support
        support_context = f"""[INCOMING SUPPORT CALL]
Customer's Sentiment: {sentiment_analysis.get('sentiment', 'neutral')}
Urgency Level: {sentiment_analysis.get('urgency', 'normal')}
Customer's Current Message: "{transcript}"

[INTELLIGENT RESPONSE GUIDELINES]

You are on a LIVE SUPPORT PHONE CALL. Adapt your response naturally based on what the customer needs:

**RESPONSE LENGTH - Adapt Based on Customer Request:**
- If customer says "tell me more", "explain", "how do I" → Give DETAILED step-by-step help (4-6 complete sentences)
- If customer asks a specific question → Answer COMPLETELY in 2-4 sentences
- If customer's question is complex (long message) → Provide thorough assistance
- If customer gives simple acknowledgment ("okay", "got it") → Brief confirmation (1-2 sentences)
- If customer is frustrated or urgent → Be direct and solution-focused immediately

**SENTENCE COMPLETION - CRITICAL:**
- ALWAYS complete every sentence
- NEVER stop mid-sentence or mid-thought
- End naturally with proper punctuation (. ! ?)
- When your solution is complete, stop naturally

**SUPPORT BEST PRACTICES:**
- Match the customer's urgency and tone
- If they need detailed help, provide it fully
- If issue is simple, keep it brief but helpful
- Use create_ticket function for issues requiring follow-up
- Always be solution-focused and clear

**IMPORTANT:**
- DO NOT use booking functions
- If customer wants to schedule, create a ticket instead
- This is a phone conversation - be conversational and empathetic
- Respond completely to what they asked
- Trust your judgment on appropriate response length
"""
        
        conversation_messages.insert(0, {
            'role': 'system',
            'content': support_context
        })
        
        if sentiment_analysis['urgency'] == 'high':
            conversation_messages.insert(0, {
                'role': 'system',
                'content': f"""[URGENT REQUEST DETECTED]
Keywords: {', '.join(sentiment_analysis['urgency_keywords'])}
Priority: HIGH

Instructions:
1. Try to find solution in documentation
2. If no immediate solution, create support ticket with high priority
3. Inform customer about ticket ID and next steps
"""
            })
        
        # Get RAG response - OpenAI decides everything
        response_chunks = []
        async for chunk in rag.get_answer(
            company_id=company_id,
            question=transcript,
            agent_id=current_agent_id,
            call_sid=call_sid,
            conversation_context=conversation_messages,
            call_type="incoming"
        ):
            response_chunks.append(chunk)
        
        llm_response = "".join(response_chunks)
        
        # Handle urgent ticket creation if needed
        if sentiment_analysis['urgency'] == 'high' and len(llm_response) < 50:
            logger.warning(f"No RAG solution for urgent request - creating ticket")
            ticket_result = await execute_function(
                function_name="create_ticket",
                arguments={
                    "title": f"Urgent: {transcript[:50]}",
                    "description": f"High urgency request: {transcript}\n\nKeywords: {', '.join(sentiment_analysis['urgency_keywords'])}",
                    "customer_phone": call_metadata.get('from_number'),
                    "priority": "high"
                },
                company_id=company_id,
                call_sid=call_sid
            )
            llm_response = f"{llm_response} {ticket_result}"
        
        # Save to transcript
        conversation_transcript.append({
            'role': 'assistant',
            'content': llm_response,
            'timestamp': datetime.utcnow().isoformat(),
            'rag_used': True
        })
        
        logger.info(f"AGENT ({current_agent_id}): '{llm_response[:100]}...'")
        
        # Save to DB
        db.add(ConversationTurn(
            call_sid=call_sid,
            role="assistant",
            content=llm_response,
            created_at=datetime.utcnow()
        ))
        db.commit()
        
        # Stream LLM response
        await stream_elevenlabs_audio_with_playback(
            websocket, stream_sid, llm_response, stop_audio_flag
        )
        
    except Exception as e:
        logger.error(f"RAG error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        if sentiment_analysis.get('urgency') == 'high':
            logger.error(f"RAG failed for urgent request - creating ticket as fallback")
            try:
                ticket_result = await execute_function(
                    function_name="create_ticket",
                    arguments={
                        "title": f"Urgent: {transcript[:50]}",
                        "description": f"High urgency request (RAG failed): {transcript}",
                        "customer_phone": call_metadata.get('from_number'),
                        "priority": "critical"
                    },
                    company_id=company_id,
                    call_sid=call_sid
                )
                await stream_elevenlabs_audio_with_playback(
                    websocket, stream_sid, ticket_result, stop_audio_flag
                )
            except Exception as ticket_error:
                logger.error(f"Ticket creation failed: {ticket_error}")
                error_msg = "I'm prioritizing your request and connecting you with a supervisor immediately."
                await stream_elevenlabs_audio_with_playback(
                    websocket, stream_sid, error_msg, stop_audio_flag
                )
        else:
            error_msg = "I'm having trouble. Could you please repeat?"
            await stream_elevenlabs_audio_with_playback(
                websocket, stream_sid, error_msg, stop_audio_flag
            )

async def process_and_respond_outbound(
    transcript: str,
    websocket: WebSocket,
    stream_sid: str,
    stop_audio_flag: dict,
    db: Session,
    call_sid: str,
    current_agent_context: dict,
    current_agent_id: str,
    company_id: str,
    conversation_transcript: list,
    intent_analysis: dict,
    call_type: str
):
    """Process outbound call and respond - fully cancellable"""
    
    rag = get_rag_service()
    
    try:
        rag_decision = should_use_rag(transcript, conversation_transcript, intent_analysis)
        
        logger.info(f"RAG Decision: {rag_decision['reason']} - Use RAG: {rag_decision['use_rag']}")
        logger.info(f"Buying Readiness: {intent_analysis.get('buying_readiness', 0)}%")

        if not rag_decision['use_rag'] and rag_decision['direct_response']:
            logger.info(f"Using direct response: '{rag_decision['direct_response']}'")
            
            conversation_transcript.append({
                'role': 'assistant',
                'content': rag_decision['direct_response'],
                'timestamp': datetime.utcnow().isoformat(),
                'rag_used': False
            })
            
            db.add(ConversationTurn(
                call_sid=call_sid,
                role="assistant",
                content=rag_decision['direct_response'],
                created_at=datetime.utcnow()
            ))
            db.commit()
            
            await stream_elevenlabs_audio_with_playback(
                websocket, stream_sid, rag_decision['direct_response'], stop_audio_flag
            )
            return

        call_metadata = call_context.get(call_sid, {})
        campaign_id = call_metadata.get('campaign_id', None)
        customer_name = call_metadata.get('customer_name', 'Customer')
        customer_phone = call_metadata.get('to_number', None)
            
        current_date = datetime.now()
        current_year = current_date.year
        tomorrow_date = (current_date + timedelta(days=1)).strftime('%Y-%m-%d')
        tomorrow_display = (current_date + timedelta(days=1)).strftime('%B %d, %Y')
        
        logger.info(f"Call Metadata - Campaign: {campaign_id}, Customer: {customer_name}, Phone: {customer_phone}")

        conversation_messages = []
        for msg in conversation_transcript[-10:]:
            if msg['role'] in ['user', 'assistant']:
                conversation_messages.append({
                    'role': msg['role'],
                    'content': msg['content']
                })
        
        buying_readiness = intent_analysis.get('buying_readiness', 0)
        objection_type = intent_analysis.get('objection_type', 'none')
        intent_type = intent_analysis.get('intent_type')

        company_name = current_agent_context.get('name', 'our company')
        services_offered = current_agent_context.get('additional_context', {}).get('businessContext', 'our services')

        persuasion_context = f"""[AI INTENT ANALYSIS]
Customer Intent: {intent_type}
Sentiment: {intent_analysis.get('sentiment')}
Buying Readiness: {buying_readiness}%
Objection Type: {objection_type}
Reasoning: {intent_analysis.get('reasoning')}
Customer's Current Message: "{transcript}"

[CALL METADATA - USE FOR BOOKING]
campaign_id: {campaign_id}
customer_name: {customer_name}
customer_phone: {customer_phone}

[CURRENT DATE & TIME CONTEXT]
Today's Date: {current_date.strftime('%Y-%m-%d')} ({current_date.strftime('%A, %B %d, %Y')})
Current Year: {current_year}
Tomorrow's Date: {tomorrow_date} ({tomorrow_display})
Business Hours: 9:00 AM to 6:00 PM (slots every 30 minutes)

[YOUR ROLE AND SCOPE]
You are a sales representative for {company_name}.
Your services: {services_offered}

**GUARDRAILS - STAY ON TOPIC:**
✅ You CAN discuss:
- {company_name}'s services and offerings
- Benefits, pricing, features of our services
- Booking appointments or consultations
- Answering questions about what we offer
- Addressing concerns or objections about our services

❌ You CANNOT discuss:
- Competitor companies or their services
- Unrelated topics (weather, news, sports, politics, etc.)
- Technical support for other products
- Personal advice outside your service scope
- Topics unrelated to {company_name}

**HANDLING OUT-OF-SCOPE QUERIES:**
If customer asks about something outside your scope:
1. Politely acknowledge: "That's an interesting question, but..."
2. Redirect: "However, I'm here to help you with [our services]. Can I tell you about..."
3. Keep it brief (1-2 sentences) and redirect back

[INTELLIGENT RESPONSE GUIDELINES]

You are on a LIVE PHONE CALL. Adapt your response naturally based on what the customer is asking:

**RESPONSE LENGTH - Adapt Based on Customer Signals:**
- If customer says "tell me more", "continue", "explain", "I want to know more" → Give DETAILED response (4-6 complete sentences with specifics)
- If customer asks a specific question → Answer COMPLETELY in 2-4 sentences
- If customer gives simple acknowledgment ("okay", "yes", "I see") → Brief 1-2 sentence follow-up or next point
- If customer shows high interest (buying readiness 70%+) → Provide thorough closing details and guide to booking
- If customer just starting (buying readiness <40%) → Keep it engaging but concise (2-3 sentences)

**SENTENCE COMPLETION - CRITICAL:**
- ALWAYS complete every sentence
- NEVER stop mid-sentence or mid-thought
- End naturally with proper punctuation (. ! ?)
- When your point is complete, stop naturally

**SALES GUIDANCE:**
- Buying Readiness 70%+ → Provide complete info and naturally ask: "Would you like to schedule a consultation?"
- Buying Readiness 40-70% → Build value with 2-3 key benefits, generate interest
- Buying Readiness <40% → Spark curiosity with 1-2 compelling points

[DATE PARSING RULES - CRITICAL]

When customer mentions a date, ALWAYS parse it correctly:

**YEAR HANDLING:**
- Current Year is: {current_year}
- If customer says "1st December" or "December 1st" → Parse as {current_year}-12-01
- If customer says "tomorrow" → Parse as {tomorrow_date}
- If customer says "next Monday" → Calculate date in {current_year}
- NEVER use past years (2022, 2023, 2024)
- ALWAYS assume {current_year} unless customer explicitly says a different year

**DATE FORMAT:**
- ALWAYS use format: YYYY-MM-DD
- Example: "1st December" = "{current_year}-12-01"
- Example: "December 15" = "{current_year}-12-15"
- Example: "tomorrow" = "{tomorrow_date}"

**MONTH CONVERSIONS:**
- January = 01, February = 02, March = 03, April = 04
- May = 05, June = 06, July = 07, August = 08
- September = 09, October = 10, November = 11, December = 12

**EXAMPLES:**
Customer: "Book on 1st December"
Correct: preferred_date="{current_year}-12-01" ✅
Wrong: preferred_date="2022-12-01" ❌

Customer: "December 25th"
Correct: preferred_date="{current_year}-12-25" ✅

Customer: "tomorrow"
Correct: preferred_date="{tomorrow_date}" ✅

[SIMPLIFIED BOOKING FLOW - WITH AVAILABILITY CHECKING]

**CRITICAL - CUSTOMER INFO IS ALREADY AVAILABLE:**
✅ Customer Name: {customer_name}
✅ Customer Phone: {customer_phone}
✅ Campaign ID: {campaign_id}

**DO NOT ASK THE CUSTOMER FOR:**
❌ Their name (you already know it's {customer_name})
❌ Their phone number (you already have it)

**ONLY ASK FOR:**
✅ Preferred date and time
✅ Email address (only after confirming slot availability)

**BOOKING CONVERSATION FLOW:**

Step 1: Customer agrees to book
Customer: "Yes, I'd like to book an appointment"
You: "Perfect! What date and time works best for you? We have availability starting from {tomorrow_display}."

Step 2: Customer provides date/time
Customer: "Book on 1st December" or "December 1st at 10 AM"

**PARSE THE DATE CORRECTLY:**
- Extract date: If they say "1st December", parse as "{current_year}-12-01"
- Extract time: If they say "10 AM", use "10:00 AM"
- If no time given, ask: "What time works for you? We have slots from 9 AM to 6 PM."

Step 3: Check availability using the function
You: [Use check_slot_availability with:
  - customer_phone: "{customer_phone}"
  - preferred_date: "{current_year}-12-01" (correctly parsed with current year)
  - preferred_time: "10:00 AM"
  - campaign_id: "{campaign_id}"
]

Step 4a: If slot is AVAILABLE
Function returns: "Great news! {current_year}-12-01 at 10:00 AM is available. To proceed, I'll need your email address..."
You: Simply relay this and ask for email

Step 4b: If slot is NOT AVAILABLE
Function returns: "I'm sorry, but {current_year}-12-01 at 10:00 AM is fully booked. Would you like me to suggest alternative times?"
You: Offer alternative slots by checking nearby times:
- Try checking the same date at different times (11 AM, 2 PM, 3 PM)
- Or try the next day at the same time
- Use check_slot_availability for each alternative

Example:
You: "I'm sorry, but December 1st at 10 AM is fully booked. Let me check some alternatives for you."
[Check {current_year}-12-01 at 11:00 AM]
[Check {current_year}-12-01 at 2:00 PM]
[Check {current_year}-12-02 at 10:00 AM]
You: "I have availability on December 1st at 11 AM or 2 PM, or December 2nd at 10 AM. Which works better for you?"

Step 5: Ask for email after confirming availability
You: "Great! That time is available. I just need your email address to send the confirmation."

Step 6: Verify email
Customer: "john@example.com"
You: [Use verify_customer_email with customer_email="john@example.com"]
You: "Let me confirm - that's j-o-h-n at example dot com, correct?"

Step 7: Create booking with ALL pre-filled information
Customer: "Yes, that's correct"
You: [Use create_booking with:
  - customer_name: "{customer_name}" (ALWAYS use this value)
  - customer_phone: "{customer_phone}" (ALWAYS use this value)
  - preferred_date: "{current_year}-12-01" (correctly parsed date)
  - preferred_time: "10:00 AM"
  - customer_email: "john@example.com" (from customer)
]

**CRITICAL INSTRUCTIONS:**
1. ALWAYS parse dates with current year: {current_year}
2. ALWAYS check availability BEFORE asking for email
3. If slot unavailable, suggest 2-3 alternatives
4. NEVER ask for name or phone - they're pre-filled
5. Use check_slot_availability for EVERY date/time before confirming

**EXAMPLE - CORRECT BOOKING WITH AVAILABILITY:**
Customer: "Book on December 1st at 10 AM"
You: [check_slot_availability(
  customer_phone="{customer_phone}",
  preferred_date="{current_year}-12-01",
  preferred_time="10:00 AM",
  campaign_id="{campaign_id}"
)]

If Available:
You: "Excellent! December 1st at 10 AM is available. I just need your email address for the confirmation."

If Not Available:
You: [Check alternatives: {current_year}-12-01 at 11:00 AM, 2:00 PM, etc.]
You: "December 1st at 10 AM is booked, but I have 11 AM or 2 PM available on the same day. Which works better?"

**REMEMBER:**
- Current year is {current_year} - NEVER use 2022, 2023, or 2024
- Always check availability before collecting email
- Suggest alternatives if requested slot is unavailable
- Only ask for EMAIL - name and phone are pre-filled
"""
        
        conversation_messages.insert(0, {
            'role': 'system',
            'content': persuasion_context
        })
        
        # Get RAG response - OpenAI decides everything
        response_chunks = []
        async for chunk in rag.get_answer(
            company_id=company_id,
            question=transcript,
            agent_id=current_agent_id,
            call_sid=call_sid,
            conversation_context=conversation_messages,
            call_type=call_type
        ):
            response_chunks.append(chunk)
        
        llm_response = "".join(response_chunks)
        
        # Save to transcript
        conversation_transcript.append({
            'role': 'assistant',
            'content': llm_response,
            'timestamp': datetime.utcnow().isoformat(),
            'intent_handled': intent_type,
            'rag_used': True
        })
        
        logger.info(f"AGENT ({current_agent_id}): '{llm_response[:100]}...'")
        
        # Save to DB
        db.add(ConversationTurn(
            call_sid=call_sid,
            role="assistant",
            content=llm_response,
            created_at=datetime.utcnow()
        ))
        db.commit()
        
        # Stream LLM response
        await stream_elevenlabs_audio_with_playback(
            websocket, stream_sid, llm_response, stop_audio_flag
        )
        
    except Exception as e:
        logger.error(f"RAG error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        error_msg = "I'm having trouble. Could you please repeat?"
        await stream_elevenlabs_audio_with_playback(
            websocket, stream_sid, error_msg, stop_audio_flag
        )

@router.post("/outbound-connect")
async def handle_outbound_connect(request: Request):
    """Outbound call handler with ElevenLabs via WebSocket"""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        from_number = form_data.get("From")
        to_number = form_data.get("To")
        
        # Store in global variables
        global from_number_global
        global to_number_global
        
        from_number_global = from_number
        to_number_global = to_number
        
        # Get parameters
        campaign_id = request.query_params.get("campaign_id")
        company_id = request.query_params.get("company_id")
        agent_id = request.query_params.get("agent_id")
        customer_name = request.query_params.get("customer_name", "")
        
        logger.info(f"OUTBOUND CALL - CallSid: {call_sid}")
        logger.info(f"From: {from_number}, To: {to_number}")
        logger.info(f"Company: {company_id}, Agent: {agent_id}")
        logger.info(f"Customer: {customer_name}, Campaign: {campaign_id}")
        
        # Generate TwiML response
        response = VoiceResponse()
        
        # Connect to Media Stream
        connect = Connect()
        ws_domain = settings.base_url.replace('https://', '').replace('http://', '')
        
        # Store context
        call_context[call_sid] = {
            "company_id": company_id,
            "agent_id": agent_id,
            "from_number": from_number,
            "to_number": to_number,
            "customer_name": customer_name,
            "campaign_id": campaign_id,
            "call_type": "outgoing"
        }
        
        stream_url = f"wss://{ws_domain}/api/v1/twilio-elevenlabs/outbound-stream?call_sid={call_sid}"
        
        logger.info(f"Stream URL: {stream_url}")
        
        connect.stream(url=stream_url)
        response.append(connect)
        
        twiml_str = str(response)
        logger.info(f"TwiML Response:\n{twiml_str}")
        
        return Response(content=twiml_str, media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        response = VoiceResponse()
        response.say("An error occurred. Please try again.", voice="Polly.Joanna")
        return Response(content=str(response), media_type="application/xml")

@router.websocket("/outbound-stream")
async def handle_outbound_stream(websocket: WebSocket):
    """Outbound call streaming with proper echo prevention and interruption handling"""
    try:
        logger.info("WebSocket connection attempt...")
        await websocket.accept()
        logger.info("WebSocket ACCEPTED")
    except Exception as e:
        logger.error(f"Failed to accept WebSocket: {str(e)}")
        return
    
    conversation_transcript = []
    call_sid = websocket.query_params.get("call_sid")
    first_message_data = None
    is_agent_speaking = False
    stop_audio_flag = {'stop': False}
    current_audio_task = None
    current_processing_task = None
    greeting_sent = False
    greeting_start_time = None
    rejection_count = 0  # Track rejections
    
    logger.info(f"Query params: {dict(websocket.query_params)}")
    
    if not call_sid:
        logger.info("No call_sid in query, waiting for Twilio 'start' event...")
        try:
            for attempt in range(3):
                message = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                data = json.loads(message)
                event_type = data.get("event")
                
                logger.info(f"Received event #{attempt + 1}: {event_type}")
                
                if event_type == "connected":
                    logger.info("Received 'connected' event")
                    continue
                elif event_type == "start":
                    call_sid = data.get("start", {}).get("callSid")
                    first_message_data = data
                    logger.info(f"Extracted call_sid: {call_sid}")
                    break
        
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for start event")
            await websocket.close(code=1008)
            return
        except Exception as e:
            logger.error(f"Error receiving messages: {str(e)}")
            await websocket.close(code=1011)
            return
    
    if not call_sid:
        logger.error("Could not obtain call_sid")
        await websocket.close(code=1008)
        return
    
    logger.info(f"Call SID validated: {call_sid}")
    
    # Get context from stored data
    context = call_context.get(call_sid, {})
    company_id = context.get("company_id")
    master_agent_id = context.get("agent_id")
    customer_name = context.get("customer_name", "")
    campaign_id = context.get("campaign_id", "")
    call_type = context.get("call_type", "outgoing")
    
    logger.info(f"Company ID: {company_id}, Master Agent: {master_agent_id}")
    logger.info(f"Customer: {customer_name}, Campaign: {campaign_id}")
    
    # Fetch master agent config
    master_agent = await agent_config_service.get_master_agent(company_id, master_agent_id)
    current_agent_context = master_agent
    
    call_metadata = {
        'start_time': datetime.utcnow(),
        'from_number': from_number_global,
        'to_number': to_number_global,
        'company_id': company_id,
        'call_type': call_type
    }
    
    logger.info(f"Detected Company Id {company_id} for storing in Call Database")
    logger.info(f"Call Type: {call_type}")
    
    if not master_agent:
        logger.error(f"Master agent {master_agent_id} not found!")
        await websocket.close(code=1008, reason="Master agent not found")
        return
    
    logger.info(f"Master: {master_agent['name']}")

    additional_context = master_agent.get('additional_context', {})
    business_context = additional_context.get('businessContext', 'our services')

    company_name = "our company"
    
    greeting = (
        f"Hello {customer_name}! This is calling from {company_name}. "
        f"I'm reaching out because we offer {business_context}. "
        f"Would you be interested in learning more about this?"
    )
    
    logger.info(f"Greeting pre-generated: '{greeting}'")
    
    # Load specialized agents
    available_agents = await agent_config_service.get_company_agents(company_id)
    specialized_agents = [
        a for a in available_agents
        if a['agent_id'] != master_agent_id
    ]
    
    logger.info(f"Master: {master_agent['name']}")
    logger.info(f"Specialized agents: {[a['name'] for a in specialized_agents]}")
    
    # Initialize services
    db = SessionLocal()
    deepgram_service = DeepgramWebSocketService()
    rag = get_rag_service()
    stream_sid = None
    
    call_state = {
        "first_interaction": True,
        "interaction_count": 0
    }
    
    try:
        async def on_deepgram_transcript(session_id: str, transcript: str):
            nonlocal conversation_transcript
            nonlocal is_agent_speaking
            nonlocal current_agent_context
            nonlocal current_audio_task
            nonlocal current_processing_task
            nonlocal stop_audio_flag
            nonlocal greeting_start_time
            nonlocal rejection_count
            
            if not transcript.strip():
                return
            
            if greeting_start_time:
                elapsed = (datetime.utcnow() - greeting_start_time).total_seconds()
                if elapsed < 3:
                    logger.debug(f"Ignoring early transcript (echo): '{transcript}' at {elapsed:.1f}s")
                    return
            
            logger.info(f"Transcript: '{transcript}' | Agent speaking: {is_agent_speaking}")
            
            if is_agent_speaking:
                word_count = len(transcript.split())
                if word_count >= 3:
                    logger.warning(f"REAL INTERRUPTION: '{transcript}'")

                    stop_audio_flag['stop'] = True

                    if current_audio_task and not current_audio_task.done():
                        logger.info("Cancelling audio playback...")
                        current_audio_task.cancel()
                        try:
                            await current_audio_task
                        except asyncio.CancelledError:
                            pass
                    
                    if current_processing_task and not current_processing_task.done():
                        logger.info("Cancelling RAG processing...")
                        current_processing_task.cancel()
                        try:
                            await current_processing_task
                        except asyncio.CancelledError:
                            pass
                    
                    # Mark agent as not speaking
                    is_agent_speaking = False
                    
                    # Wait for buffer clear
                    logger.info("Waiting for Twilio to clear...")
                    await asyncio.sleep(1.0)
                    
                    # Reset flag
                    stop_audio_flag['stop'] = False
                    logger.info("✓ Ready for new input")
                else:
                    logger.debug(f"Ignoring short utterance: '{transcript}' ({word_count} words)")
                    return
            
            logger.info(f"CUSTOMER SAID: '{transcript}'")
            
            # Detect customer intent for outbound calls
            intent_analysis = await intent_detection_service.detect_customer_intent(
                customer_message=transcript,
                conversation_history=conversation_transcript,
                call_type=call_type
            )
            
            # Save to transcript
            conversation_transcript.append({
                'role': 'user',
                'content': transcript,
                'timestamp': datetime.utcnow().isoformat(),
                'intent_type': intent_analysis.get('intent_type'),
                'sentiment': intent_analysis.get('sentiment'),
                'buying_readiness': intent_analysis.get('buying_readiness')
            })
            
            # Save to DB
            try:
                db.add(ConversationTurn(
                    call_sid=call_sid,
                    role="user",
                    content=transcript,
                    created_at=datetime.utcnow()
                ))
                db.commit()
            except Exception as e:
                logger.error(f"DB error: {e}")
            
            intent_type = intent_analysis.get('intent_type')
            buying_readiness = intent_analysis.get('buying_readiness', 0)
            should_book = intent_analysis.get('should_book', False)
            should_persuade = intent_analysis.get('should_persuade', True)
            should_end_call = intent_analysis.get('should_end_call', False)
            
            logger.info(f"PERSUASION MODE (Intent: {intent_type}, Readiness: {buying_readiness}%)")

            if intent_type == 'rejection':
                rejection_count += 1
                logger.warning(f"Rejection #{rejection_count} detected")
                
                if rejection_count >= 2:
                    logger.info(f"2 REJECTIONS DETECTED - Ending call gracefully")
                    
                    farewell_message = (
                        f"I completely understand, {customer_name}. "
                        f"Thank you for your time today. If you change your mind or have any questions in the future, "
                        f"please don't hesitate to reach out. Have a wonderful day!"
                    )
                    
                    conversation_transcript.append({
                        'role': 'assistant',
                        'content': farewell_message,
                        'timestamp': datetime.utcnow().isoformat(),
                        'call_ended': True
                    })
                    
                    try:
                        db.add(ConversationTurn(
                            call_sid=call_sid,
                            role="assistant",
                            content=farewell_message,
                            created_at=datetime.utcnow()
                        ))
                        db.commit()
                    except Exception as e:
                        logger.error(f"DB error: {e}")
                    
                    is_agent_speaking = True
                    stop_audio_flag['stop'] = False
                    
                    current_audio_task = asyncio.create_task(
                        stream_elevenlabs_audio_with_playback(websocket, stream_sid, farewell_message, stop_audio_flag)
                    )
                    
                    try:
                        await current_audio_task
                    except asyncio.CancelledError:
                        logger.info("Farewell message cancelled")
                    finally:
                        is_agent_speaking = False
                        current_audio_task = None
                    
                    # End the call
                    logger.info("Ending call after 2 rejections")
                    return
            else:
                # Reset rejection count if customer shows interest
                if intent_type in ['soft_interest', 'strong_buying', 'question']:
                    rejection_count = 0
                    logger.info("Customer engaged - reset rejection count")

            current_agent_id = master_agent_id
            
            if specialized_agents:
                logger.info(f"Detecting intent for message #{call_state['interaction_count'] + 1}...")
                
                detected_agent = await intent_router_service.detect_intent(
                    transcript,
                    company_id,
                    master_agent,
                    specialized_agents
                )
                
                if detected_agent:
                    previous_agent_id = intent_router_service.get_current_agent(call_sid, master_agent_id)
                    intent_router_service.set_current_agent(call_sid, detected_agent)
                    current_agent_id = detected_agent
                    
                    agent_info = await agent_config_service.get_agent_by_id(detected_agent)
                    if agent_info:
                        current_agent_context = agent_info
                        
                        if detected_agent != previous_agent_id and call_state["interaction_count"] > 0:
                            logger.info(f"RE-ROUTING to {agent_info['name']}")
                            
                            routing_message = f"Let me connect you with our {agent_info['name']}."
                            
                            is_agent_speaking = True
                            stop_audio_flag['stop'] = False
                            
                            current_audio_task = asyncio.create_task(
                                stream_elevenlabs_audio_with_playback(websocket, stream_sid, routing_message, stop_audio_flag)
                            )
                            
                            await asyncio.sleep(0.5)
                            
                            try:
                                await current_audio_task
                            except asyncio.CancelledError:
                                logger.info("Routing message cancelled")
                            finally:
                                is_agent_speaking = False
                                current_audio_task = None
                        else:
                            logger.info(f"ROUTED to {agent_info['name']}")
                else:
                    logger.info(f"   Staying with MASTER")
            
            call_state["interaction_count"] += 1

            is_agent_speaking = True
            await asyncio.sleep(0.3)
            stop_audio_flag['stop'] = False
            
            current_processing_task = asyncio.create_task(
                process_and_respond_outbound(
                    transcript=transcript,
                    websocket=websocket,
                    stream_sid=stream_sid,
                    stop_audio_flag=stop_audio_flag,
                    db=db,
                    call_sid=call_sid,
                    current_agent_context=current_agent_context,
                    current_agent_id=current_agent_id,
                    company_id=company_id,
                    conversation_transcript=conversation_transcript,
                    intent_analysis=intent_analysis,
                    call_type=call_type
                )
            )
            
            try:
                await current_processing_task
                logger.info("✓ Response completed")
            except asyncio.CancelledError:
                logger.info("Response cancelled by interruption")
            finally:
                is_agent_speaking = False
                current_audio_task = None
                current_processing_task = None

        session_id = f"deepgram_{call_sid}"
        logger.info(f"Initializing Deepgram...")
        
        deepgram_start = datetime.utcnow()
        deepgram_connected = await deepgram_service.initialize_session(
            session_id=session_id,
            callback=on_deepgram_transcript
        )
        deepgram_duration = (datetime.utcnow() - deepgram_start).total_seconds()
        
        if not deepgram_connected:
            logger.error(f"Deepgram connection failed")
            await websocket.close()
            return
        
        logger.info(f"Deepgram connected in {deepgram_duration:.2f}s")

        if first_message_data and first_message_data.get("event") == "start":
            stream_sid = first_message_data.get("streamSid")
            logger.info(f"STREAM STARTED: {stream_sid}")

            logger.info(f"Sending greeting immediately...")
            
            conversation_transcript.append({
                'role': 'assistant',
                'content': greeting,
                'timestamp': datetime.utcnow().isoformat()
            })
            
            is_agent_speaking = True
            stop_audio_flag['stop'] = False
            greeting_start_time = datetime.utcnow()
            
            greeting_audio_start = datetime.utcnow()
            
            current_audio_task = asyncio.create_task(
                stream_elevenlabs_audio_with_playback(websocket, stream_sid, greeting, stop_audio_flag)
            )
            
            try:
                await current_audio_task
                greeting_total_duration = (datetime.utcnow() - greeting_audio_start).total_seconds()
                logger.info(f"Greeting completed in {greeting_total_duration:.2f}s - ready for customer response")
            except asyncio.CancelledError:
                logger.info("Greeting cancelled")
            finally:
                is_agent_speaking = False
                current_audio_task = None
            
            greeting_sent = True
            await asyncio.sleep(0.5)
        
        logger.info("Entering message loop...")
        
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                data = json.loads(message)
                event = data.get("event")
                
                if event == "connected":
                    logger.debug("Connected event")
                
                elif event == "start":
                    if not greeting_sent:
                        stream_sid = data.get("streamSid")
                        logger.info(f"STREAM STARTED (delayed): {stream_sid}")

                        logger.info(f"Sending greeting...")
                        
                        conversation_transcript.append({
                            'role': 'assistant',
                            'content': greeting,
                            'timestamp': datetime.utcnow().isoformat()
                        })
                        
                        is_agent_speaking = True
                        stop_audio_flag['stop'] = False
                        greeting_start_time = datetime.utcnow()
                        
                        current_audio_task = asyncio.create_task(
                            stream_elevenlabs_audio_with_playback(websocket, stream_sid, greeting, stop_audio_flag)
                        )
                        
                        try:
                            await current_audio_task
                            logger.info("Greeting completed")
                        except asyncio.CancelledError:
                            pass
                        finally:
                            is_agent_speaking = False
                            current_audio_task = None
                        
                        greeting_sent = True
                        await asyncio.sleep(0.5)
                
                elif event == "media":
                    # Process user audio for interruption detection
                    payload = data.get("media", {}).get("payload")
                    if payload:
                        audio = await deepgram_service.convert_twilio_audio(payload, session_id)
                        if audio:
                            await deepgram_service.process_audio_chunk(session_id, audio)
                
                elif event == "stop":
                    logger.info(f"STREAM STOPPED")
                    break
            
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected")
                break
            except Exception as e:
                logger.error(f"Error: {str(e)}")
                break
    
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
    
    finally:
        logger.info(f"Cleaning up {call_sid}")
        logger.info(f"From: {call_metadata.get('from_number')}")
        logger.info(f"To: {call_metadata.get('to_number')}")
        logger.info(f"Company ID: {call_metadata.get('company_id')}")
        
        try:
            call_duration = 0
            if call_metadata.get('start_time'):
                call_duration = int((datetime.utcnow() - call_metadata['start_time']).total_seconds())
            
            logger.info(f"Uploading call data to S3...")
            logger.info(f"Duration: {call_duration}s")
            logger.info(f"Transcript turns: {len(conversation_transcript)}")
            
            s3_urls = await call_recording_service.save_call_data(
                call_sid=call_sid,
                company_id=company_id,
                agent_id=master_agent_id,
                transcript=conversation_transcript,
                recording_url=None,
                duration=call_duration,
                from_number=call_metadata.get('from_number'),
                to_number=call_metadata.get('to_number')
            )
            
            logger.info(f"S3 Upload Complete:")
            logger.info(f"Transcript: {s3_urls.get('transcript_url')}")
            
            try:
                call_record = db.query(Call).filter_by(call_sid=call_sid).first()
                
                if call_record:
                    call_record.transcription = s3_urls.get('transcript_url')
                    call_record.company_id = s3_urls.get('company_id')
                    call_record.from_number = s3_urls.get('from_number')
                    call_record.to_number = s3_urls.get('to_number')
                    call_record.duration = call_duration
                    call_record.status = 'completed'
                    call_record.ended_at = datetime.utcnow()
                    db.commit()
                    logger.info(f"Database updated with S3 URLs")
                else:
                    new_call = Call(
                        call_sid=call_sid,
                        company_id=call_metadata.get('company_id'),
                        from_number=call_metadata.get('from_number'),
                        to_number=call_metadata.get('to_number'),
                        status='completed',
                        duration=call_duration,
                        transcription=s3_urls.get('transcript_url'),
                        created_at=call_metadata['start_time'],
                        ended_at=datetime.utcnow()
                    )
                    db.add(new_call)
                    db.commit()
                    logger.info(f"Call record created")
            
            except Exception as db_error:
                logger.error(f"Database update error: {db_error}")
                db.rollback()
        
        except Exception as upload_error:
            logger.error(f"S3 upload error: {upload_error}")
            import traceback
            logger.error(traceback.format_exc())
        
        intent_router_service.clear_call(call_sid)
        
        try:
            await deepgram_service.close_session(session_id)
        except:
            pass
        
        call_context.pop(call_sid, None)
        db.close()
        
        logger.info(f"Cleanup complete")


@router.post("/initiate-outbound-call")
async def initiate_outbound_call(request: Request):
    """Initiate an outbound call to a customer"""
    try:
        data = await request.json()
        
        to_number = data.get("to_number")
        customer_name = data.get("customer_name", "")
        company_id = data.get("company_id")
        agent_id = data.get("agent_id")
        campaign_id = data.get("campaign_id", "")
        from_number = data.get("from_number", settings.twilio_phone_number)
        
        if not to_number or not company_id or not agent_id:
            return {
                "success": False,
                "error": "Missing required fields: to_number, company_id, agent_id"
            }
        
        logger.info(f"Initiating outbound call to {to_number}")
        logger.info(f"Customer: {customer_name}, Company: {company_id}, Agent: {agent_id}")
        logger.info(f"Campaign: {campaign_id}")
        
        # Build the callback URL with query parameters
        ws_domain = settings.base_url
        callback_url = (
            f"{ws_domain}/api/v1/twilio-elevenlabs/outbound-connect"
            f"?company_id={company_id}"
            f"&agent_id={agent_id}"
            f"&customer_name={quote(customer_name)}"
            f"&campaign_id={campaign_id}"
        )
        
        logger.info(f"Callback URL: {callback_url}")
        
        # Initiate the call using Twilio
        call = twilio_client.calls.create(
            to=to_number,
            from_=from_number,
            url=callback_url,
            method='POST',
            status_callback=f"{ws_domain}/api/v1/twilio/call-status",
            status_callback_event=['initiated', 'ringing', 'answered', 'completed'],
            status_callback_method='POST',
            record=False,  # We handle recording via stream
            timeout=30,  # Ring timeout in seconds
            machine_detection='DetectMessageEnd',  # Detect answering machines
            machine_detection_timeout=5,
            machine_detection_speech_threshold=2000,
            machine_detection_speech_end_threshold=1200,
            machine_detection_silence_timeout=5000
        )
        
        logger.info(f"Call initiated successfully: {call.sid}")
        
        # Store initial call record in database
        db = SessionLocal()
        try:
            new_call = Call(
                call_sid=call.sid,
                company_id=company_id,
                from_number=from_number,
                to_number=to_number,
                call_type=CallType.outgoing,
                status='initiated',
                created_at=datetime.utcnow()
            )
            db.add(new_call)
            db.commit()
            logger.info(f"Call record created in database: {call.sid}")
        except Exception as db_error:
            logger.error(f"Failed to create call record: {db_error}")
            db.rollback()
        finally:
            db.close()
        
        return {
            "success": True,
            "call_sid": call.sid,
            "to_number": to_number,
            "from_number": from_number,
            "customer_name": customer_name,
            "campaign_id": campaign_id,
            "status": call.status
        }
        
    except Exception as e:
        logger.error(f"Error initiating outbound call: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        return {
            "success": False,
            "error": str(e)
        }

