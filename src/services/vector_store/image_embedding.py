from typing import List, Dict, Optional, Any
import logging
from datetime import datetime
import base64
from PIL import Image
import io
import uuid
import httpx
from qdrant_client import models
from langchain_openai import OpenAIEmbeddings
from config.settings import settings

logger = logging.getLogger(__name__)

class ImageEmbeddingService:
    def __init__(self, qdrant_service):
        """Initialize Image Embedding Service"""
        self.qdrant_service = qdrant_service
        self.embeddings = OpenAIEmbeddings(
            model="text-embedding-3-small",
            openai_api_key=settings.openai_api_key
        )
        
        self.openai_api_key = settings.openai_api_key
    
    async def get_image_description(self, image_content: bytes) -> str:
        """Get image description using OpenAI's Vision API"""
        try:
            # Convert image to base64
            base64_image = base64.b64encode(image_content).decode('utf-8')
            
            # Prepare the API request
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.openai_api_key}"
            }
            
            payload = {
                "model": "gpt-4o-mini",  # Using the current model name for vision
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Please provide a detailed description of this image, including key elements, colors, composition, and any text or notable features."
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}",
                                    "detail": "high"  # Request high detail analysis
                                }
                            }
                        ]
                    }
                ],
                "max_tokens": 300
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    result = response.json()
                    description = result['choices'][0]['message']['content']
                    logger.info(f"Generated description: {description[:100]}...")
                    return description
                else:
                    error_message = f"Error from OpenAI API: {response.text}"
                    logger.error(error_message)
                    
                    # Fallback description if API fails
                    return """Error generating image description. Using fallback description:
                    This is an uploaded image. For detailed information about its contents, 
                    please refer to any user-provided description."""
                    
        except Exception as e:
            error_message = f"Error getting image description: {str(e)}"
            logger.error(error_message)
            return error_message
    
    async def process_image(
        self,
        image_content: bytes,
        user_description: Optional[str] = None
    ) -> Dict[str, Any]:
        """Process image and generate embeddings with descriptions"""
        try:
            # Get image details
            image = Image.open(io.BytesIO(image_content))
            
            # Get OpenAI description
            auto_description = await self.get_image_description(image_content)
            
            # Combine descriptions
            combined_description = f"""
            User Description: {user_description if user_description else 'Not provided'}
            
            AI Description: {auto_description}
            
            Image Details:
            - Size: {image.size}
            - Mode: {image.mode}
            - Format: {image.format}
            """
            
            # Generate embedding
            embedding = await self.embeddings.aembed_query(combined_description)
            
            return {
                "description": combined_description,
                "embedding": embedding,
                "metadata": {
                    "has_user_description": bool(user_description),
                    "image_size": image.size,
                    "image_mode": image.mode,
                    "image_format": image.format,
                    "processed_at": datetime.utcnow().isoformat()
                }
            }
            
        except Exception as e:
            logger.error(f"Error processing image: {str(e)}")
            raise
    
    async def embed_images(
        self,
        company_id: str,
        agent_id: str,
        images: List[Dict[str, Any]]
    ) -> bool:
        """Process and embed images into the vector database"""
        try:
            logger.info(f"Embedding {len(images)} images for agent {agent_id}")
            
            # Process each image
            points = []
            for image_data in images:
                try:
                    # Process image
                    processed = await self.process_image(
                        image_content=image_data['content'],
                        user_description=image_data.get('description')
                    )
                    
                    # Create point
                    points.append(models.PointStruct(
                        id=str(uuid.uuid4()),
                        vector=processed['embedding'],
                        payload={
                            'page_content': processed['description'],
                            'metadata': {
                                **processed['metadata'],
                                'agent_id': agent_id,
                                'document_id': image_data['id'],
                                'original_filename': image_data.get('filename'),
                                'content_type': image_data.get('content_type'),
                                'type': 'image'
                            }
                        }
                    ))
                    
                except Exception as e:
                    logger.error(f"Error processing image {image_data.get('id')}: {str(e)}")
                    continue
            
            if not points:
                logger.warning("No images processed successfully")
                return True
                
            # Add points to Qdrant
            result = await self.qdrant_service.add_points(company_id, points)
            
            logger.info(f"Embedded {len(points)} images for agent {agent_id}")
            return result
            
        except Exception as e:
            logger.error(f"Error embedding images: {str(e)}")
            return False