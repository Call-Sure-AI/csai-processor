#!/usr/bin/env python3
"""
Test script for Multi-Provider LLM Service
"""
import asyncio
import sys
import os

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.llm_service import llm_service

async def test_llm_service():
    """Test the multi-provider LLM service"""
    print("🤖 Testing Multi-Provider LLM Service")
    print("=" * 50)
    
    # Test 1: Basic response generation
    print("\n1. Testing basic response generation...")
    try:
        response = await llm_service.generate_response([
            {"role": "user", "content": "Hello, how are you?"}
        ])
        print(f"✅ Response: {response}")
    except Exception as e:
        print(f"❌ Error: {str(e)}")
    
    # Test 2: Greeting generation
    print("\n2. Testing greeting generation...")
    try:
        greeting = await llm_service.generate_greeting()
        print(f"✅ Greeting: {greeting}")
    except Exception as e:
        print(f"❌ Error: {str(e)}")
    
    # Test 3: Sentiment analysis
    print("\n3. Testing sentiment analysis...")
    try:
        sentiment = await llm_service.analyze_sentiment("I'm very happy with the service!")
        print(f"✅ Sentiment: {sentiment}")
    except Exception as e:
        print(f"❌ Error: {str(e)}")
    
    # Test 4: Provider availability
    print("\n4. Checking provider availability...")
    providers = {
        "Claude": bool(llm_service.claude_api_key),
        "OpenAI": bool(llm_service.openai_api_key),
        "Fallback": True
    }
    
    for provider, available in providers.items():
        status = "✅ Available" if available else "❌ Not Available"
        print(f"   {provider}: {status}")
    
    print("\n" + "=" * 50)
    print("🎉 LLM Service Test Complete!")

if __name__ == "__main__":
    asyncio.run(test_llm_service())
