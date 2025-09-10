import os
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional, BinaryIO
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from fastapi import UploadFile
import logging

logger = logging.getLogger(__name__)

class S3Config:
    def __init__(
        self,
        region: str = None,
        access_key_id: str = None,
        secret_access_key: str = None,
        bucket_name: str = None
    ):
        self.region = region or os.getenv('AWS_REGION', 'us-east-1')
        self.access_key_id = access_key_id or os.getenv('AWS_ACCESS_KEY_ID')
        self.secret_access_key = secret_access_key or os.getenv('AWS_SECRET_ACCESS_KEY')
        self.bucket_name = bucket_name or os.getenv('S3_BUCKET_NAME')

class S3Handler:
    def __init__(self, config: S3Config = None):
        self.config = config or S3Config()
        
        if not all([self.config.access_key_id, self.config.secret_access_key, self.config.bucket_name]):
            raise ValueError("AWS credentials and bucket name are required")
        
        self.s3_client = boto3.client(
            's3',
            region_name=self.config.region,
            aws_access_key_id=self.config.access_key_id,
            aws_secret_access_key=self.config.secret_access_key
        )
        self.bucket_name = self.config.bucket_name

    async def upload_file(
        self, 
        file: UploadFile, 
        enable_public_read_access: bool = True,
        custom_key: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            if custom_key:
                key = custom_key
            else:
                timestamp = int(datetime.now().timestamp() * 1000)
                key = f"{timestamp}-{file.filename}"

            file_content = await file.read()

            await file.seek(0)

            upload_params = {
                'Bucket': self.bucket_name,
                'Key': key,
                'Body': file_content,
                'ContentType': file.content_type
            }

            if enable_public_read_access:
                upload_params['ACL'] = 'public-read'
            
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, 
                lambda: self.s3_client.put_object(**upload_params)
            )

            url = f"https://{self.bucket_name}.s3.{self.config.region}.amazonaws.com/{key}"
            
            return {
                "success": True,
                "key": key,
                "url": url,
                "data": {
                    "etag": result.get('ETag'),
                    "version_id": result.get('VersionId'),
                    "size": len(file_content)
                }
            }
            
        except NoCredentialsError:
            logger.error("AWS credentials not found")
            return {
                "success": False,
                "error": "AWS credentials not configured"
            }
        except ClientError as e:
            logger.error(f"S3 upload error: {e}")
            return {
                "success": False,
                "error": str(e)
            }
        except Exception as e:
            logger.error(f"S3 upload error: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def upload_multiple_files(self, files: List[UploadFile]) -> List[Dict[str, Any]]:
        logger.info(f"Uploading {len(files)} files")
        
        tasks = [
            self.upload_file(file, enable_public_read_access=False) 
            for file in files
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed_results.append({
                    "success": False,
                    "error": str(result),
                    "file_index": i
                })
            else:
                processed_results.append(result)
        
        return processed_results

    async def get_file_details(self, key: str) -> Dict[str, Any]:
        try:
            # Use head_object to get metadata
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.s3_client.head_object(
                    Bucket=self.bucket_name,
                    Key=key
                )
            )
            
            return {
                "success": True,
                "key": key,
                "size": response.get('ContentLength', 0),
                "content_type": response.get('ContentType'),
                "last_modified": response.get('LastModified'),
                "etag": response.get('ETag'),
                "metadata": response.get('Metadata', {}),
                "url": f"https://{self.bucket_name}.s3.{self.config.region}.amazonaws.com/{key}"
            }
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == '403':
                logger.error(f"Access denied for S3 object: {key}")
                return {
                    "success": False,
                    "error": "Access denied - check S3 permissions"
                }
            elif error_code == '404':
                logger.error(f"S3 object not found: {key}")
                return {
                    "success": False,
                    "error": "File not found"
                }
            else:
                logger.error(f"S3 error: {e}")
                return {
                    "success": False,
                    "error": str(e)
                }


    async def download_file(self, key: str) -> Dict[str, Any]:
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self.s3_client.get_object(Bucket=self.bucket_name, Key=key)
            )
            
            file_content = result['Body'].read()
            
            return {
                "success": True,
                "data": file_content,
                "content_type": result.get('ContentType'),
                "metadata": result.get('Metadata', {}),
                "size": len(file_content)
            }
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                return {
                    "success": False,
                    "error": "File not found"
                }
            return {
                "success": False,
                "error": str(e)
            }
        except Exception as e:
            logger.error(f"Download file error: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def delete_file(self, key: str) -> Dict[str, Any]:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.delete_object(Bucket=self.bucket_name, Key=key)
            )
            
            return {
                "success": True,
                "message": "File deleted successfully"
            }
            
        except ClientError as e:
            return {
                "success": False,
                "error": str(e)
            }
        except Exception as e:
            logger.error(f"Delete file error: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def delete_multiple_files(self, keys: List[str]) -> List[Dict[str, Any]]:
        tasks = [self.delete_file(key) for key in keys]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed_results.append({
                    "success": False,
                    "error": str(result),
                    "key": keys[i]
                })
            else:
                processed_results.append({**result, "key": keys[i]})
        
        return processed_results

    async def list_files(
        self, 
        prefix: str = "", 
        max_keys: int = 1000
    ) -> Dict[str, Any]:
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self.s3_client.list_objects_v2(
                    Bucket=self.bucket_name,
                    Prefix=prefix,
                    MaxKeys=max_keys
                )
            )
            
            files = []
            if 'Contents' in result:
                for item in result['Contents']:
                    files.append({
                        "key": item['Key'],
                        "size": item['Size'],
                        "last_modified": item['LastModified'],
                        "etag": item['ETag'],
                        "url": f"https://{self.bucket_name}.s3.amazonaws.com/{item['Key']}"
                    })
            
            return {
                "success": True,
                "files": files,
                "count": len(files),
                "is_truncated": result.get('IsTruncated', False),
                "next_continuation_token": result.get('NextContinuationToken')
            }
            
        except ClientError as e:
            return {
                "success": False,
                "error": str(e)
            }
        except Exception as e:
            logger.error(f"List files error: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def copy_file(self, source_key: str, destination_key: str) -> Dict[str, Any]:
        try:
            copy_source = {
                'Bucket': self.bucket_name,
                'Key': source_key
            }
            
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self.s3_client.copy_object(
                    CopySource=copy_source,
                    Bucket=self.bucket_name,
                    Key=destination_key
                )
            )
            
            return {
                "success": True,
                "source": source_key,
                "destination": destination_key,
                "data": {
                    "etag": result.get('ETag'),
                    "version_id": result.get('VersionId')
                }
            }
            
        except ClientError as e:
            return {
                "success": False,
                "error": str(e)
            }
        except Exception as e:
            logger.error(f"Copy file error: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def file_exists(self, key: str) -> Dict[str, Any]:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.s3_client.head_object(Bucket=self.bucket_name, Key=key)
            )
            
            return {
                "success": True,
                "exists": True
            }
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey' or e.response['Error']['Code'] == '404':
                return {
                    "success": True,
                    "exists": False
                }
            return {
                "success": False,
                "error": str(e)
            }
        except Exception as e:
            logger.error(f"File exists check error: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def generate_presigned_url(
        self, 
        key: str, 
        expiration: int = 3600,
        http_method: str = 'GET'
    ) -> Dict[str, Any]:
        try:
            loop = asyncio.get_event_loop()
            url = await loop.run_in_executor(
                None,
                lambda: self.s3_client.generate_presigned_url(
                    'get_object' if http_method == 'GET' else 'put_object',
                    Params={'Bucket': self.bucket_name, 'Key': key},
                    ExpiresIn=expiration
                )
            )
            
            return {
                "success": True,
                "url": url,
                "expires_in": expiration
            }
            
        except ClientError as e:
            return {
                "success": False,
                "error": str(e)
            }
        except Exception as e:
            logger.error(f"Generate presigned URL error: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def get_file_url(self, key: str, public: bool = True) -> str:
        if public:
            return f"https://{self.bucket_name}.s3.{self.config.region}.amazonaws.com/{key}"
        else:
            result = await self.generate_presigned_url(key)
            return result.get('url', '') if result['success'] else ''
