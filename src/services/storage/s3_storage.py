"""
The backend is now fully integrated with aioboto3 for asynchronous S3 operations.

1. Upload File (upload_file_to_s3)
    - Uploads a file to an S3 bucket asynchronously.
    - Uses aioboto3.Session.client("s3") to initialize an async S3 client.
    - Returns the public S3 URL of the uploaded file.
2. Download File (download_file_from_s3)
    - Downloads a file from an S3 bucket to a local destination.
    - Handles errors gracefully and raises exceptions with descriptive messages.
3. List Files (list_files_in_s3)
    - Lists all files in an S3 bucket with an optional prefix.
    - Returns a list of file keys present in the bucket.

Note:
Benefits of Using aioboto3
    - Native Async Support: aioboto3 provides an async-native interface for
        AWS S3, making it easier to integrate with async frameworks like FastAPI.
    - Concurrency: Multiple file uploads/downloads or S3 operations can run in
        parallel, improving performance.
    - Clean and Readable Code: Async APIs make the code concise and easier
        to manage compared to wrapping synchronous methods in asyncio.
"""

import aioboto3
from botocore.exceptions import NoCredentialsError
from config.settings import S3_BUCKET_NAME, S3_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY


async def upload_file_to_s3(file_path: str, key: str) -> str:
    """
    Upload a file to S3 asynchronously using aioboto3 and return the S3 URL.
    """
    session = aioboto3.Session(
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=S3_REGION,
    )
    async with session.client("s3") as s3_client:
        try:
            await s3_client.upload_file(file_path, S3_BUCKET_NAME, key)
            return f"https://{S3_BUCKET_NAME}.s3.{S3_REGION}.amazonaws.com/{key}"
        except Exception as e:
            raise RuntimeError(f"Error uploading to S3: {e}") from e

async def async_upload_to_s3(bucket: str, key: str, content: bytes, content_type: str = "application/json") -> None:
    """
    Asynchronously upload content to S3.
    
    Args:
        bucket (str): S3 bucket name.
        key (str): S3 object key.
        content (bytes): File content.
        content_type (str): Content type of the uploaded object.
    
    Returns:
        None
    """
    session = aioboto3.Session(
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=S3_REGION,
    )
    async with session.client("s3") as s3_client:
        try:
            await s3_client.put_object(
                Bucket=bucket,
                Key=key,
                Body=content,
                ContentType=content_type
            )
        except NoCredentialsError as exc:
            raise RuntimeError("AWS credentials not available.") from exc
        except Exception as e:
            raise RuntimeError(f"Failed to upload to S3: {e}") from e


async def download_file_from_s3(key: str, destination_path: str) -> None:
    """
    Download a file from S3 asynchronously using aioboto3 to the specified destination path.
    """
    session = aioboto3.Session(
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=S3_REGION,
    )
    async with session.client("s3") as s3_client:
        try:
            await s3_client.download_file(S3_BUCKET_NAME, key, destination_path)
        except Exception as e:
            raise RuntimeError(f"Error downloading from S3: {e}") from e


async def list_files_in_s3(prefix: str = "") -> list:
    """
    List all files in an S3 bucket with the given prefix asynchronously.
    """
    session = aioboto3.Session(
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=S3_REGION,
    )
    async with session.client("s3") as s3_client:
        try:
            response = await s3_client.list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix=prefix)
            if 'Contents' in response:
                return [content["Key"] for content in response["Contents"]]
            return []
        except Exception as e:
            raise RuntimeError(f"Error listing files in S3: {e}") from e
