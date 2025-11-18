# check_documents.py
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from dotenv import load_dotenv
import os
from collections import defaultdict

# Load environment variables
load_dotenv()

# Configuration
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = os.getenv("QDRANT_PORT", "6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "voice_agent_documents")

# Company and Agent IDs to check
COMPANY_ID = "43aacf2b-d8e8-4ece-a75c-43a66100842c"
AGENT_ID = "63741786-d82f-446d-894f-ec6ab2b50654"  # Change this to your agent ID

def check_documents():
    """Check what documents exist in Qdrant"""
    print(f"\n{'='*80}")
    print(f"Connecting to Qdrant at {QDRANT_HOST}:{QDRANT_PORT}")
    print(f"Collection: {COLLECTION_NAME}")
    print(f"{'='*80}\n")
    
    try:
        # Connect to Qdrant
        client = QdrantClient(
            url=f"http://{QDRANT_HOST}:{QDRANT_PORT}",
            api_key=QDRANT_API_KEY,
            timeout=10
        )
        
        # Check collection exists
        collections = client.get_collections()
        collection_names = [col.name for col in collections.collections]
        
        print(f"📦 Available collections: {collection_names}\n")
        
        if COLLECTION_NAME not in collection_names:
            print(f"❌ Collection '{COLLECTION_NAME}' not found!")
            return
        
        # Get collection info
        collection_info = client.get_collection(COLLECTION_NAME)
        print(f"📊 Collection Stats:")
        print(f"   Total vectors: {collection_info.vectors_count}")
        print(f"   Indexed vectors: {collection_info.indexed_vectors_count}")
        print(f"   Status: {collection_info.status}\n")
        
        # Check all documents for this company
        print(f"🔍 Searching for company: {COMPANY_ID}\n")
        
        result = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                        key="company_id",
                        match=MatchValue(value=COMPANY_ID)
                    )
                ]
            ),
            limit=1000,
            with_payload=True,
            with_vectors=False
        )
        
        points = result[0]
        print(f"📄 Found {len(points)} chunks for company {COMPANY_ID}\n")
        
        if not points:
            print("❌ No documents found for this company!")
            print("   - Check if documents were uploaded")
            print("   - Verify company_id matches")
            return
        
        # Group by agent and document
        agents = defaultdict(lambda: defaultdict(int))
        documents_by_agent = defaultdict(set)
        
        for point in points:
            payload = point.payload
            agent_id = payload.get("agent_id", "None")
            doc_name = payload.get("document_name", "Unknown")
            
            agents[agent_id]["chunks"] += 1
            documents_by_agent[agent_id].add(doc_name)
        
        # Display results
        print(f"{'='*80}")
        print("📋 DOCUMENTS BY AGENT:")
        print(f"{'='*80}\n")
        
        for agent_id, stats in agents.items():
            print(f"🤖 Agent ID: {agent_id or 'NULL'}")
            print(f"   Total chunks: {stats['chunks']}")
            print(f"   Documents:")
            for doc in sorted(documents_by_agent[agent_id]):
                doc_chunks = sum(1 for p in points 
                               if p.payload.get("agent_id") == agent_id 
                               and p.payload.get("document_name") == doc)
                print(f"      • {doc} ({doc_chunks} chunks)")
            print()
        
        # Check specific agent
        if AGENT_ID:
            print(f"{'='*80}")
            print(f"🎯 CHECKING SPECIFIC AGENT: {AGENT_ID}")
            print(f"{'='*80}\n")
            
            agent_result = client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="company_id",
                            match=MatchValue(value=COMPANY_ID)
                        ),
                        FieldCondition(
                            key="agent_id",
                            match=MatchValue(value=AGENT_ID)
                        )
                    ]
                ),
                limit=1000,
                with_payload=True,
                with_vectors=False
            )
            
            agent_points = agent_result[0]
            
            if agent_points:
                print(f"✅ Found {len(agent_points)} chunks for agent {AGENT_ID}")
                
                # Show document breakdown
                docs = defaultdict(int)
                for point in agent_points:
                    doc_name = point.payload.get("document_name", "Unknown")
                    docs[doc_name] += 1
                
                print(f"\n📚 Documents:")
                for doc_name, count in sorted(docs.items()):
                    print(f"   • {doc_name}: {count} chunks")
                
                # Show sample chunk
                sample = agent_points[0]
                print(f"\n📄 Sample chunk:")
                print(f"   Document: {sample.payload.get('document_name')}")
                print(f"   Content preview: {sample.payload.get('page_content', '')[:200]}...")
                
            else:
                print(f"❌ No documents found for agent {AGENT_ID}")
                print(f"\n💡 Available agent IDs for this company:")
                for agent_id in agents.keys():
                    print(f"   • {agent_id or 'NULL'}")
        
        print(f"\n{'='*80}")
        print("✅ Document check complete!")
        print(f"{'='*80}\n")
        
    except Exception as e:
        print(f"\n❌ Error: {str(e)}\n")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check_documents()
