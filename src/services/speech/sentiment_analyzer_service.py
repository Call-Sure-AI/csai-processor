import asyncio
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum
import numpy as np
from collections import deque
import re
import json
from datetime import datetime, timedelta

class SentimentLevel(Enum):
    """Sentiment classification levels"""
    VERY_NEGATIVE = "very_negative"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    POSITIVE = "positive"
    VERY_POSITIVE = "very_positive"

class EmotionalState(Enum):
    """Detected emotional states"""
    ANGRY = "angry"
    FRUSTRATED = "frustrated"
    CONFUSED = "confused"
    NEUTRAL = "neutral"
    SATISFIED = "satisfied"
    HAPPY = "happy"
    ANXIOUS = "anxious"

@dataclass
class SentimentScore:
    """Sentiment analysis result"""
    score: float  # -1.0 to 1.0
    confidence: float  # 0.0 to 1.0
    level: SentimentLevel
    emotional_state: EmotionalState
    keywords: List[str]
    timestamp: datetime

class SentimentAnalyzer:
    """Real-time sentiment analysis for phone conversations"""
    
    def __init__(self, provider: str = "local", enable_voice_features: bool = True):
        self.provider = provider
        self.enable_voice_features = enable_voice_features
        
        # Sentiment history for trend analysis
        self.sentiment_history = deque(maxlen=20)
        
        # Emotional trigger words/phrases
        self.negative_indicators = {
            "very_negative": [
                "terrible", "awful", "horrible", "worst", "unacceptable",
                "disgusting", "pathetic", "ridiculous", "waste of time"
            ],
            "negative": [
                "bad", "poor", "disappointed", "unhappy", "frustrated",
                "annoyed", "confused", "difficult", "problem", "issue"
            ]
        }
        
        self.positive_indicators = {
            "positive": [
                "good", "nice", "helpful", "thank you", "pleased",
                "satisfied", "works", "solved", "better"
            ],
            "very_positive": [
                "excellent", "amazing", "fantastic", "wonderful", "perfect",
                "brilliant", "outstanding", "best", "love it"
            ]
        }
        
        # Escalation phrases that indicate frustration
        self.escalation_phrases = [
            "speak to manager", "supervisor", "human agent", "real person",
            "this is ridiculous", "wasting my time", "going in circles",
            "already told you", "not listening", "don't understand"
        ]
        
        # Initialize ML model if using external provider
        self.model = None
        if provider != "local":
            asyncio.create_task(self._initialize_ml_model())
    
    async def _initialize_ml_model(self):
        """Initialize external sentiment analysis model"""
        if self.provider == "azure":
            from azure.ai.textanalytics import TextAnalyticsClient
            from azure.core.credentials import AzureKeyCredential
            
            self.model = TextAnalyticsClient(
                endpoint=settings.azure_cognitive_endpoint,
                credential=AzureKeyCredential(settings.azure_cognitive_key)
            )
        elif self.provider == "aws":
            import boto3
            self.model = boto3.client('comprehend', region_name='us-east-1')
        elif self.provider == "huggingface":
            from transformers import pipeline
            self.model = pipeline(
                "sentiment-analysis",
                model="distilbert-base-uncased-finetuned-sst-2-english"
            )
    
    async def analyze(
        self, 
        text: str, 
        voice_features: Optional[Dict] = None
    ) -> float:
        """
        Analyze sentiment from text and optional voice features
        Returns a score between 0.0 (very negative) and 1.0 (very positive)
        """
        # Get text-based sentiment
        text_sentiment = await self._analyze_text(text)
        
        # Adjust based on voice features if available
        if voice_features and self.enable_voice_features:
            voice_adjustment = self._analyze_voice_features(voice_features)
            # Weighted combination: 70% text, 30% voice
            final_score = (text_sentiment.score * 0.7) + (voice_adjustment * 0.3)
        else:
            final_score = text_sentiment.score
        
        # Normalize to 0-1 range
        normalized_score = (final_score + 1.0) / 2.0
        
        # Add to history
        self.sentiment_history.append({
            "score": normalized_score,
            "timestamp": datetime.utcnow(),
            "text": text[:100]  # Store truncated text
        })
        
        return normalized_score
    
    async def _analyze_text(self, text: str) -> SentimentScore:
        """Analyze sentiment from text content"""
        if self.provider == "local":
            return self._local_sentiment_analysis(text)
        else:
            return await self._external_sentiment_analysis(text)
    
    def _local_sentiment_analysis(self, text: str) -> SentimentScore:
        """Rule-based local sentiment analysis"""
        text_lower = text.lower()
        
        # Initialize scores
        positive_score = 0
        negative_score = 0
        detected_keywords = []
        
        # Check for very negative indicators
        for phrase in self.negative_indicators["very_negative"]:
            if phrase in text_lower:
                negative_score += 2
                detected_keywords.append(phrase)
        
        # Check for negative indicators
        for phrase in self.negative_indicators["negative"]:
            if phrase in text_lower:
                negative_score += 1
                detected_keywords.append(phrase)
        
        # Check for positive indicators
        for phrase in self.positive_indicators["positive"]:
            if phrase in text_lower:
                positive_score += 1
                detected_keywords.append(phrase)
        
        # Check for very positive indicators
        for phrase in self.positive_indicators["very_positive"]:
            if phrase in text_lower:
                positive_score += 2
                detected_keywords.append(phrase)
        
        # Check for escalation phrases (strong negative signal)
        for phrase in self.escalation_phrases:
            if phrase in text_lower:
                negative_score += 3
                detected_keywords.append(f"ESCALATION: {phrase}")
        
        # Calculate final score
        if positive_score + negative_score == 0:
            score = 0.0  # Neutral
            level = SentimentLevel.NEUTRAL
            emotional_state = EmotionalState.NEUTRAL
        else:
            score = (positive_score - negative_score) / (positive_score + negative_score)
            
            # Determine level
            if score <= -0.6:
                level = SentimentLevel.VERY_NEGATIVE
                emotional_state = EmotionalState.ANGRY
            elif score <= -0.2:
                level = SentimentLevel.NEGATIVE
                emotional_state = EmotionalState.FRUSTRATED
            elif score <= 0.2:
                level = SentimentLevel.NEUTRAL
                emotional_state = EmotionalState.NEUTRAL
            elif score <= 0.6:
                level = SentimentLevel.POSITIVE
                emotional_state = EmotionalState.SATISFIED
            else:
                level = SentimentLevel.VERY_POSITIVE
                emotional_state = EmotionalState.HAPPY
        
        # Check for specific emotional states
        if "confused" in text_lower or "don't understand" in text_lower:
            emotional_state = EmotionalState.CONFUSED
        elif "worried" in text_lower or "anxious" in text_lower:
            emotional_state = EmotionalState.ANXIOUS
        
        return SentimentScore(
            score=score,
            confidence=min(0.9, (positive_score + negative_score) / 10),
            level=level,
            emotional_state=emotional_state,
            keywords=detected_keywords[:5],  # Top 5 keywords
            timestamp=datetime.utcnow()
        )
    
    async def _external_sentiment_analysis(self, text: str) -> SentimentScore:
        """Use external API for sentiment analysis"""
        try:
            if self.provider == "azure" and self.model:
                response = self.model.analyze_sentiment([text])[0]
                
                score = response.confidence_scores.positive - response.confidence_scores.negative
                
                # Map Azure sentiment to our scale
                if response.sentiment == "positive":
                    level = SentimentLevel.POSITIVE
                    emotional_state = EmotionalState.SATISFIED
                elif response.sentiment == "negative":
                    level = SentimentLevel.NEGATIVE
                    emotional_state = EmotionalState.FRUSTRATED
                else:
                    level = SentimentLevel.NEUTRAL
                    emotional_state = EmotionalState.NEUTRAL
                
                return SentimentScore(
                    score=score,
                    confidence=max(
                        response.confidence_scores.positive,
                        response.confidence_scores.negative,
                        response.confidence_scores.neutral
                    ),
                    level=level,
                    emotional_state=emotional_state,
                    keywords=[],
                    timestamp=datetime.utcnow()
                )
                
            elif self.provider == "aws" and self.model:
                response = self.model.detect_sentiment(
                    Text=text,
                    LanguageCode='en'
                )
                
                sentiment = response['Sentiment']
                scores = response['SentimentScore']
                
                # Calculate score from AWS response
                score = scores['Positive'] - scores['Negative']
                
                # Map AWS sentiment
                sentiment_map = {
                    'POSITIVE': (SentimentLevel.POSITIVE, EmotionalState.SATISFIED),
                    'NEGATIVE': (SentimentLevel.NEGATIVE, EmotionalState.FRUSTRATED),
                    'NEUTRAL': (SentimentLevel.NEUTRAL, EmotionalState.NEUTRAL),
                    'MIXED': (SentimentLevel.NEUTRAL, EmotionalState.CONFUSED)
                }
                
                level, emotional_state = sentiment_map.get(
                    sentiment, 
                    (SentimentLevel.NEUTRAL, EmotionalState.NEUTRAL)
                )
                
                return SentimentScore(
                    score=score,
                    confidence=max(scores.values()),
                    level=level,
                    emotional_state=emotional_state,
                    keywords=[],
                    timestamp=datetime.utcnow()
                )
                
        except Exception as e:
            logger.error(f"External sentiment analysis failed: {str(e)}")
            # Fallback to local analysis
            return self._local_sentiment_analysis(text)
        
        # Default fallback
        return self._local_sentiment_analysis(text)
    
    def _analyze_voice_features(self, voice_features: Dict) -> float:
        """
        Analyze sentiment from voice features like pitch, tone, speed
        Returns score between -1.0 and 1.0
        """
        score = 0.0
        
        # Analyze pitch variance (high variance often indicates emotion)
        if "pitch_variance" in voice_features:
            variance = voice_features["pitch_variance"]
            if variance > 100:  # High variance
                # Could be excitement (positive) or anger (negative)
                # Need to combine with other features
                score += 0.1
        
        # Analyze speaking rate
        if "speaking_rate" in voice_features:
            rate = voice_features["speaking_rate"]
            if rate > 180:  # Fast speaking
                score -= 0.2  # Often indicates frustration or anger
            elif rate < 120:  # Slow speaking
                score -= 0.1  # May indicate sadness or confusion
        
        # Analyze volume/energy
        if "energy" in voice_features:
            energy = voice_features["energy"]
            if energy > 0.8:  # High energy
                score -= 0.2  # Often anger or frustration
            elif energy < 0.3:  # Low energy
                score -= 0.1  # May indicate disengagement
        
        # Analyze voice quality
        if "voice_quality" in voice_features:
            quality = voice_features["voice_quality"]
            if quality == "tense":
                score -= 0.3
            elif quality == "breathy":
                score -= 0.1
            elif quality == "normal":
                score += 0.1
        
        return max(-1.0, min(1.0, score))
    
    def get_sentiment_trend(self, window_seconds: int = 60) -> Dict[str, Any]:
        """
        Analyze sentiment trend over time window
        Returns trend analysis with recommendations
        """
        if not self.sentiment_history:
            return {
                "trend": "neutral",
                "average_score": 0.5,
                "direction": "stable",
                "recommendation": None
            }
        
        cutoff_time = datetime.utcnow() - timedelta(seconds=window_seconds)
        recent_sentiments = [
            s for s in self.sentiment_history 
            if s["timestamp"] > cutoff_time
        ]
        
        if len(recent_sentiments) < 2:
            return {
                "trend": "insufficient_data",
                "average_score": recent_sentiments[0]["score"] if recent_sentiments else 0.5,
                "direction": "stable",
                "recommendation": None
            }
        
        # Calculate trend
        scores = [s["score"] for s in recent_sentiments]
        avg_score = np.mean(scores)
        
        # Calculate direction using linear regression
        x = np.arange(len(scores))
        slope = np.polyfit(x, scores, 1)[0]
        
        # Determine trend direction
        if slope > 0.1:
            direction = "improving"
        elif slope < -0.1:
            direction = "declining"
        else:
            direction = "stable"
        
        # Generate recommendations
        recommendation = self._generate_recommendation(avg_score, direction)
        
        return {
            "trend": self._score_to_trend(avg_score),
            "average_score": float(avg_score),
            "direction": direction,
            "slope": float(slope),
            "recommendation": recommendation,
            "data_points": len(recent_sentiments)
        }
    
    def _score_to_trend(self, score: float) -> str:
        """Convert score to trend description"""
        if score < 0.3:
            return "very_negative"
        elif score < 0.45:
            return "negative"
        elif score < 0.55:
            return "neutral"
        elif score < 0.7:
            return "positive"
        else:
            return "very_positive"
    
    def _generate_recommendation(self, avg_score: float, direction: str) -> Optional[str]:
        """Generate action recommendation based on sentiment"""
        if avg_score < 0.3:
            if direction == "declining":
                return "URGENT: Consider immediate escalation to human agent"
            else:
                return "Show empathy and offer immediate assistance"
        elif avg_score < 0.45:
            if direction == "declining":
                return "Acknowledge frustration and offer alternatives"
            else:
                return "Continue with patient assistance"
        elif direction == "declining" and avg_score < 0.6:
            return "Check if customer needs clarification"
        
        return None
    
    def detect_escalation_needed(self) -> bool:
        """
        Detect if escalation to human agent is needed
        Based on sentiment history and patterns
        """
        if len(self.sentiment_history) < 3:
            return False
        
        # Check recent sentiment scores
        recent_scores = [s["score"] for s in list(self.sentiment_history)[-5:]]
        avg_recent = np.mean(recent_scores)
        
        # Escalate if consistently very negative
        if avg_recent < 0.25:
            return True
        
        # Check for rapid decline
        if len(recent_scores) >= 3:
            decline_rate = recent_scores[-1] - recent_scores[-3]
            if decline_rate < -0.4:  # Rapid decline
                return True
        
        # Check for escalation phrases in recent history
        recent_texts = [s.get("text", "") for s in list(self.sentiment_history)[-3:]]
        for text in recent_texts:
            for phrase in self.escalation_phrases[:5]:  # Check top escalation phrases
                if phrase in text.lower():
                    return True
        
        return False
    
    def get_emotional_summary(self) -> Dict[str, Any]:
        """Get summary of emotional journey through the call"""
        if not self.sentiment_history:
            return {
                "dominant_emotion": EmotionalState.NEUTRAL.value,
                "emotional_range": 0.0,
                "stability": 1.0,
                "peak_positive": None,
                "peak_negative": None
            }
        
        scores = [s["score"] for s in self.sentiment_history]
        
        # Calculate metrics
        emotional_range = max(scores) - min(scores)
        stability = 1.0 - np.std(scores) if len(scores) > 1 else 1.0
        
        # Find peaks
        peak_positive_idx = np.argmax(scores)
        peak_negative_idx = np.argmin(scores)
        
        # Determine dominant emotion
        avg_score = np.mean(scores)
        if avg_score < 0.3:
            dominant = EmotionalState.FRUSTRATED
        elif avg_score < 0.45:
            dominant = EmotionalState.CONFUSED
        elif avg_score < 0.55:
            dominant = EmotionalState.NEUTRAL
        elif avg_score < 0.7:
            dominant = EmotionalState.SATISFIED
        else:
            dominant = EmotionalState.HAPPY
        
        return {
            "dominant_emotion": dominant.value,
            "emotional_range": float(emotional_range),
            "stability": float(stability),
            "peak_positive": {
                "score": float(scores[peak_positive_idx]),
                "text": self.sentiment_history[peak_positive_idx].get("text", "")
            },
            "peak_negative": {
                "score": float(scores[peak_negative_idx]),
                "text": self.sentiment_history[peak_negative_idx].get("text", "")
            },
            "average_score": float(avg_score)
        }
    
    def reset(self):
        """Reset sentiment history for new conversation"""
        self.sentiment_history.clear()

class AdaptiveResponseGenerator:
    """Generate responses adapted to customer sentiment"""
    
    def __init__(self, sentiment_analyzer: SentimentAnalyzer):
        self.sentiment_analyzer = sentiment_analyzer
        
        self.empathy_templates = {
            EmotionalState.ANGRY: [
                "I completely understand your frustration, and I apologize for this experience.",
                "I can hear this has been really frustrating for you, and I'm sorry about that."
            ],
            EmotionalState.FRUSTRATED: [
                "I understand this is frustrating. Let me help you resolve this quickly.",
                "I apologize for the inconvenience. Let's get this sorted out right away."
            ],
            EmotionalState.CONFUSED: [
                "Let me clarify that for you.",
                "I'll explain this step by step to make it clearer."
            ],
            EmotionalState.ANXIOUS: [
                "I understand your concern. Let me help you with this.",
                "Don't worry, we'll work through this together."
            ]
        }
    
    def adapt_response(self, base_response: str, sentiment_score: float) -> str:
        """Adapt response based on customer sentiment"""
        trend = self.sentiment_analyzer.get_sentiment_trend(30)
        
        # Get appropriate empathy prefix if needed
        prefix = ""
        if sentiment_score < 0.4:
            emotional_summary = self.sentiment_analyzer.get_emotional_summary()
            dominant_emotion = EmotionalState(emotional_summary["dominant_emotion"])
            
            if dominant_emotion in self.empathy_templates:
                templates = self.empathy_templates[dominant_emotion]
                prefix = templates[0] + " "
        
        # Adjust response tone
        if sentiment_score < 0.3:
            # Very negative - be extra careful and professional
            response = self._make_more_empathetic(base_response)
        elif trend["direction"] == "declining":
            # Getting worse - acknowledge and reassure
            response = self._add_reassurance(base_response)
        else:
            response = base_response
        
        return prefix + response
    
    def _make_more_empathetic(self, text: str) -> str:
        """Make response more empathetic"""
        replacements = {
            "You need to": "If you could please",
            "You should": "I'd recommend",
            "You must": "It would be helpful to",
            "Can't": "I'm unable to",
            "Won't": "I'm not able to"
        }
        
        for old, new in replacements.items():
            text = text.replace(old, new)
        
        return text
    
    def _add_reassurance(self, text: str) -> str:
        """Add reassuring language"""
        if not text.endswith("."):
            text += "."
        text += " I'm here to help you through this."
        return text