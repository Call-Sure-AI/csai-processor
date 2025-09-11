# test_s3.py
import boto3
import os
from dotenv import load_dotenv

load_dotenv()

# Test with explicit credentials
s3_client = boto3.client(
    's3',
    aws_access_key_id=os.getenv('aws_access_key_id'),
    aws_secret_access_key=os.getenv('aws_secret_access_key'),
    region_name='eu-north-1'
)

bucket = 'beta-csai-processor'
key = '1757547730486-leads.csv'

try:
    # Test 1: Can we access the bucket?
    print("Testing bucket access...")
    response = s3_client.list_objects_v2(Bucket=bucket, MaxKeys=1)
    print(f"✓ Can list bucket: {response.get('Name')}")
except Exception as e:
    print(f"✗ Cannot list bucket: {e}")

try:
    # Test 2: Can we head the object?
    print(f"\nTesting object access: {key}")
    response = s3_client.head_object(Bucket=bucket, Key=key)
    print(f"✓ Object exists: Size={response['ContentLength']} bytes")
except Exception as e:
    print(f"✗ Cannot access object: {e}")

try:
    # Test 3: Can we get the object?
    print(f"\nTrying to download object...")
    response = s3_client.get_object(Bucket=bucket, Key=key)
    print(f"✓ Can download object: {response['ContentLength']} bytes")
except Exception as e:
    print(f"✗ Cannot download object: {e}")