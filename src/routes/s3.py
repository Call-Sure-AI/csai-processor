from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Query
from typing import List, Optional
from handlers.s3_handler import S3Handler, S3Config
#from middleware.auth_middleware import get_current_user
#from app.models.schemas import UserResponse
from fastapi.responses import StreamingResponse
import io
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/s3", tags=["s3"])

s3_handler = S3Handler()

def get_s3_handler() -> S3Handler:
    return S3Handler()

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    enable_public_read_access: bool = Form(True),
    custom_key: Optional[str] = Form(None),
    #current_user: UserResponse = Depends(get_current_user)
):
    try:
        result = await s3_handler.upload_file(
            file=file,
            enable_public_read_access=enable_public_read_access,
            custom_key=custom_key
        )
        
        if result["success"]:
            return result
        else:
            raise HTTPException(status_code=400, detail=result["error"])
            
    except Exception as e:
        logger.error(f"Error in upload_file: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/upload-multiple", response_model=None)
async def upload_multiple_files(
    files: List[UploadFile] = File(...),
    #current_user: UserResponse = Depends(get_current_user)
):
    try:
        results = await s3_handler.upload_multiple_files(files)
        
        successful_uploads = [r for r in results if r.get("success")]
        failed_uploads = [r for r in results if not r.get("success")]
        
        return {
            "total_files": len(files),
            "successful_uploads": len(successful_uploads),
            "failed_uploads": len(failed_uploads),
            "results": results
        }
        
    except Exception as e:
        logger.error(f"Error in upload_multiple_files: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/file-details/{key:path}", response_model=None)
async def get_file_details(
    key: str,
    #current_user: UserResponse = Depends(get_current_user),
    s3_handler: S3Handler = Depends(get_s3_handler)
):
    try:
        result = await s3_handler.get_file_details(key)
        
        if result["success"]:
            return result
        else:
            status_code = 404 if "not found" in result["error"].lower() else 400
            raise HTTPException(status_code=status_code, detail=result["error"])
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_file_details: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/download/{key:path}")
async def download_file(
    key: str,
    #current_user: UserResponse = Depends(get_current_user)
):
    try:
        result = await s3_handler.download_file(key)
        
        if result["success"]:
            file_stream = io.BytesIO(result["data"])
            
            return StreamingResponse(
                io.BytesIO(result["data"]),
                media_type=result.get("content_type", "application/octet-stream"),
                headers={
                    "Content-Disposition": f"attachment; filename={key.split('/')[-1]}"
                }
            )
        else:
            status_code = 404 if "not found" in result["error"].lower() else 400
            raise HTTPException(status_code=status_code, detail=result["error"])
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in download_file: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.delete("/delete/{key:path}")
async def delete_file(
    key: str,
    #current_user: UserResponse = Depends(get_current_user)
):
    try:
        result = await s3_handler.delete_file(key)
        
        if result["success"]:
            return result
        else:
            raise HTTPException(status_code=400, detail=result["error"])
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in delete_file: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.delete("/delete-multiple")
async def delete_multiple_files(
    keys: List[str],
    #current_user: UserResponse = Depends(get_current_user)
):
    try:
        results = await s3_handler.delete_multiple_files(keys)
        
        successful_deletions = [r for r in results if r.get("success")]
        failed_deletions = [r for r in results if not r.get("success")]
        
        return {
            "total_files": len(keys),
            "successful_deletions": len(successful_deletions),
            "failed_deletions": len(failed_deletions),
            "results": results
        }
        
    except Exception as e:
        logger.error(f"Error in delete_multiple_files: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/list")
async def list_files(
    prefix: str = Query("", description="File prefix to filter by"),
    max_keys: int = Query(1000, description="Maximum number of files to return"),
    #current_user: UserResponse = Depends(get_current_user)
):
    try:
        result = await s3_handler.list_files(prefix=prefix, max_keys=max_keys)
        
        if result["success"]:
            return result
        else:
            raise HTTPException(status_code=400, detail=result["error"])
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in list_files: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/copy")
async def copy_file(
    source_key: str = Form(...),
    destination_key: str = Form(...),
    #current_user: dict = Depends(get_current_user)
):
    try:
        result = await s3_handler.copy_file(source_key, destination_key)
        
        if result["success"]:
            return result
        else:
            raise HTTPException(status_code=400, detail=result["error"])
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in copy_file: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/exists/{key:path}")
async def file_exists(
    key: str,
    #current_user: dict = Depends(get_current_user)
):
    try:
        result = await s3_handler.file_exists(key)
        
        if result["success"]:
            return result
        else:
            raise HTTPException(status_code=400, detail=result["error"])
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in file_exists: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/presigned-url")
async def generate_presigned_url(
    key: str = Form(...),
    expiration: int = Form(3600, description="URL expiration in seconds"),
    http_method: str = Form("GET", description="HTTP method (GET or PUT)"),
    #current_user: dict = Depends(get_current_user)
):
    try:
        result = await s3_handler.generate_presigned_url(
            key=key,
            expiration=expiration,
            http_method=http_method
        )
        
        if result["success"]:
            return result
        else:
            raise HTTPException(status_code=400, detail=result["error"])
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in generate_presigned_url: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/file-url/{key:path}")
async def get_file_url(
    key: str,
    public: bool = Query(True, description="Whether to return public URL or presigned URL"),
    #current_user: dict = Depends(get_current_user)
):
    try:
        url = await s3_handler.get_file_url(key, public=public)
        
        return {
            "success": True,
            "url": url,
            "public": public
        }
        
    except Exception as e:
        logger.error(f"Error in get_file_url: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
