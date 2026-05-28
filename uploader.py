"""
YouTube Uploader - Handles OAuth2 + video upload to YouTube.

Features:
- Resumable uploads (handles large files + network interruptions)
- Auto-retry with exponential backoff
- Thumbnail upload
- Local quota telemetry (YouTube API = 10,000 units/day)
- Token refresh handling
"""

import os
import time
import json
import logging
import pickle
from pathlib import Path
from typing import Optional

import google.oauth2.credentials
import google_auth_oauthlib.flow
import googleapiclient.discovery
import googleapiclient.errors
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request

logger = logging.getLogger("RevenueMachine.YouTubeUploader")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]
TOKEN_FILE = "youtube_token.pickle"
QUOTA_FILE = "quota_usage.json"
MAX_RETRIES = 3
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]


class YouTubeQuotaExceededError(RuntimeError):
    """Raised only when YouTube API explicitly returns quota exceeded."""


class YouTubeUploader:
    def __init__(self, config: dict):
        self.config = config
        self.client_secrets = config.get("YOUTUBE_CLIENT_SECRETS", "client_secrets.json")
        self.youtube = self._get_authenticated_service()
        self.quota_tracker = QuotaTracker()
        logger.info("Repo quota guard is non-blocking; API response is authoritative for quota errors.")

    def upload(self, video_path: Path, thumbnail_path: Optional[Path], metadata: dict) -> str:
        """
        Uploads a video to YouTube.
        Returns the video ID.
        """
        logger.info(f"  📤 Uploading: {video_path.name}")

        # Local tracker can drift from real Google quota. Never hard-block here.
        if not self.quota_tracker.can_upload():
            logger.warning(
                "REPO_SIDE_QUOTA_GUARD_TRIGGERED: local tracker projects daily limit reached. "
                "Continuing upload and relying on YouTube API response."
            )

        # Build video resource
        body = {
            "snippet": {
                "title": metadata["title"],
                "description": metadata["description"],
                "tags": metadata.get("tags", []),
                "categoryId": metadata.get("categoryId", "22"),
            },
            "status": {
                "privacyStatus": metadata.get("privacyStatus", "public"),
                "selfDeclaredMadeForKids": False,
                "madeForKids": False,
            },
        }
        publish_at = metadata.get("publishAt")
        if publish_at:
            body["status"]["publishAt"] = publish_at

        # Create resumable upload
        insert_request = self.youtube.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=MediaFileUpload(
                str(video_path),
                chunksize=1024 * 1024,  # 1MB chunks
                resumable=True,
            ),
        )

        video_id = self._resumable_upload(insert_request)

        if video_id:
            # Upload thumbnail
            if thumbnail_path and thumbnail_path.exists():
                self._upload_thumbnail(video_id, thumbnail_path)

            # Add to playlist if configured
            playlist_id = metadata.get("playlist_id")
            if playlist_id:
                self._add_to_playlist(video_id, playlist_id)

            # Track quota usage (upload = 1600 units)
            self.quota_tracker.record_upload()

            logger.info(f"  ✅ Uploaded: https://youtube.com/shorts/{video_id}")
            return video_id
        else:
            raise RuntimeError("Upload failed after all retries")

    def _resumable_upload(self, insert_request) -> Optional[str]:
        """Handles resumable upload with retry logic."""
        response = None
        error = None
        retry = 0

        while response is None:
            try:
                logger.info(f"  ⏳ Uploading... (attempt {retry + 1})")
                status, response = insert_request.next_chunk()

                if status:
                    progress = int(status.progress() * 100)
                    logger.info(f"  📊 Upload progress: {progress}%")

            except googleapiclient.errors.HttpError as e:
                if e.resp.status in RETRIABLE_STATUS_CODES:
                    error = f"Retriable HTTP error {e.resp.status}: {e.content}"
                elif self._is_quota_exceeded_error(e):
                    quota_message = self._extract_quota_message(e)
                    logger.error(f"REAL_YOUTUBE_API_QUOTA_EXCEEDED: {quota_message}")
                    raise YouTubeQuotaExceededError(quota_message) from e
                else:
                    logger.error(f"Non-retriable HTTP error: {e}")
                    raise

            except Exception as e:
                error = f"Connection error: {e}"

            if error:
                retry += 1
                if retry > MAX_RETRIES:
                    logger.error(f"Max retries exceeded. Error: {error}")
                    return None

                wait_time = 2 ** retry
                logger.warning(f"  ⚠️  {error}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
                error = None

        return response.get("id")

    @staticmethod
    def _extract_http_error_payload(e: googleapiclient.errors.HttpError) -> dict:
        content = e.content
        if isinstance(content, bytes):
            try:
                content = content.decode("utf-8", errors="ignore")
            except Exception:
                content = ""
        if isinstance(content, str):
            try:
                payload = json.loads(content)
                if isinstance(payload, dict):
                    return payload
            except Exception:
                return {}
        return {}

    @classmethod
    def _is_quota_exceeded_error(cls, e: googleapiclient.errors.HttpError) -> bool:
        if e.resp.status not in (403, 429):
            return False

        payload = cls._extract_http_error_payload(e)
        reasons = []
        for err in payload.get("error", {}).get("errors", []):
            if isinstance(err, dict):
                reasons.append(str(err.get("reason", "")))
        joined = " ".join(reasons).lower()
        return "quota" in joined or "dailylimitexceeded" in joined

    @classmethod
    def _extract_quota_message(cls, e: googleapiclient.errors.HttpError) -> str:
        payload = cls._extract_http_error_payload(e)
        message = str(payload.get("error", {}).get("message", "")).strip()
        if message:
            return message
        return str(e)

    def _upload_thumbnail(self, video_id: str, thumbnail_path: Path):
        """Uploads a custom thumbnail."""
        try:
            self.youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumbnail_path)),
            ).execute()
            logger.info(f"  🖼️  Thumbnail uploaded")
            # Thumbnail upload = 50 quota units
            self.quota_tracker.record_thumbnail()
        except Exception as e:
            logger.warning(f"  Thumbnail upload failed: {e}")

    def _add_to_playlist(self, video_id: str, playlist_id: str):
        """Adds video to a playlist."""
        try:
            self.youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                },
            ).execute()
            logger.info(f"  📋 Added to playlist: {playlist_id}")
        except Exception as e:
            logger.warning(f"  Playlist add failed: {e}")

    def _get_authenticated_service(self):
        """Gets authenticated YouTube service with token refresh."""
        credentials = None

        # Load existing token
        if Path(TOKEN_FILE).exists():
            with open(TOKEN_FILE, "rb") as f:
                credentials = pickle.load(f)

        # Refresh expired token
        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
                logger.info("  🔄 Token refreshed")
            except Exception as e:
                logger.warning(f"Token refresh failed: {e}. Re-authenticating...")
                credentials = None

        # Run OAuth flow if no valid credentials
        if not credentials or not credentials.valid:
            if not Path(self.client_secrets).exists():
                raise FileNotFoundError(
                    f"client_secrets.json not found at '{self.client_secrets}'\n"
                    "Download it from: https://console.cloud.google.com/apis/credentials\n"
                    "Enable YouTube Data API v3, create OAuth 2.0 credentials."
                )

            flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
                self.client_secrets, SCOPES
            )
            # run_local_server = opens browser for OAuth
            credentials = flow.run_local_server(port=0)
            logger.info("  ✅ YouTube authenticated!")

            # Save token for future use
            with open(TOKEN_FILE, "wb") as f:
                pickle.dump(credentials, f)

        return googleapiclient.discovery.build("youtube", "v3", credentials=credentials)


class QuotaTracker:
    """Tracks local estimate of daily YouTube API usage (telemetry only)."""
    DAILY_LIMIT = 9500  # Keep buffer
    UPLOAD_COST = 1600
    THUMBNAIL_COST = 50

    def __init__(self):
        self.data = self._load()

    def can_upload(self) -> bool:
        self._reset_if_new_day()
        return self.data["used"] + self.UPLOAD_COST < self.DAILY_LIMIT

    def record_upload(self):
        self.data["used"] += self.UPLOAD_COST
        self._save()

    def record_thumbnail(self):
        self.data["used"] += self.THUMBNAIL_COST
        self._save()

    def _reset_if_new_day(self):
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        if self.data.get("date") != today:
            self.data = {"date": today, "used": 0}
            self._save()

    def _load(self) -> dict:
        if Path(QUOTA_FILE).exists():
            try:
                with open(QUOTA_FILE) as f:
                    return json.load(f)
            except Exception:
                pass
        from datetime import datetime
        return {"date": datetime.now().strftime("%Y-%m-%d"), "used": 0}

    def _save(self):
        with open(QUOTA_FILE, "w") as f:
            json.dump(self.data, f)
