import asyncio
import logging
from datetime import datetime

import httpx
from fastapi import UploadFile
from sqlalchemy.orm import Session

from config.settings import settings
from database.config import SessionLocal
from database.models import Call
from handlers.s3_handler import S3Handler

logger = logging.getLogger(__name__)


class TwilioRecordingService:
    """
    Handles:
    Twilio Recording → Download → Upload to S3 → Update Call table
    """

    def __init__(self):
        self.s3_handler = S3Handler()

    async def process_recording(
        self,
        call_sid: str,
        recording_sid: str,
        recording_url: str,
        duration: float,
    ):
        logger.info(
            "Processing recording | call_sid=%s recording_sid=%s",
            call_sid,
            recording_sid,
        )

        await asyncio.sleep(2)

        try:
            audio_bytes = await self._download_recording(
                recording_sid, recording_url
            )

            s3_url = await self._upload_to_s3(
                call_sid, recording_sid, audio_bytes
            )

            await self._update_call_record(call_sid, s3_url, duration)

            logger.info(
                "Recording stored successfully | call_sid=%s s3_url=%s",
                call_sid,
                s3_url,
            )

        except Exception:
            logger.exception(
                "Failed to process recording | call_sid=%s recording_sid=%s",
                call_sid,
                recording_sid,
            )

    async def _download_recording(
        self, recording_sid: str, recording_url: str
    ) -> bytes:
        """
        Download recording audio from Twilio.
        """
        final_url = f"{recording_url}.mp3"

        logger.info("Downloading Twilio recording | url=%s", final_url)

        async with httpx.AsyncClient(
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            timeout=60,
        ) as client:
            response = await client.get(final_url)
            response.raise_for_status()

        logger.info(
            "Recording downloaded | recording_sid=%s size=%d bytes",
            recording_sid,
            len(response.content),
        )

        return response.content

    async def _upload_to_s3(
        self, call_sid: str, recording_sid: str, audio_bytes: bytes
    ) -> str:
        """
        Upload recording to S3 using existing S3Handler.
        """
        key = f"calls/{call_sid}/recordings/{recording_sid}.mp3"

        upload_file = UploadFile(
            filename=f"{recording_sid}.mp3",
            file=None,
            content_type="audio/mpeg",
        )

        async def _read():
            return audio_bytes

        upload_file.read = _read

        logger.info("Uploading recording to S3 | key=%s", key)

        result = await self.s3_handler.upload_file(
            file=upload_file,
            enable_public_read_access=True,
            custom_key=key,
        )

        if not result.get("success"):
            raise RuntimeError(
                f"S3 upload failed: {result.get('error')}"
            )

        return result["url"]


    async def _update_call_record(
        self, call_sid: str, s3_url: str, duration: float
    ):
        """
        Persist recording URL in Call table.
        """
        db: Session = SessionLocal()

        try:
            call = db.query(Call).filter(Call.call_sid == call_sid).first()

            if not call:
                logger.warning(
                    "Call not found while updating recording | call_sid=%s",
                    call_sid,
                )
                return

            # Idempotency guard
            if call.recording_url:
                logger.info(
                    "Recording already exists, skipping | call_sid=%s",
                    call_sid,
                )
                return

            call.recording_url = s3_url
            call.duration = duration
            call.updated_at = datetime.utcnow()

            db.commit()

            logger.info(
                "Call updated with recording URL | call_sid=%s",
                call_sid,
            )

        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
