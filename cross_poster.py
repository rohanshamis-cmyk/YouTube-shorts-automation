"""
Cross-Poster - Automatically posts to TikTok and Instagram Reels.

Strategy: Post the same video to 3 platforms = 3x the reach.
  - YouTube Shorts (primary)
  - TikTok (via unofficial API / browser automation)
  - Instagram Reels (via Meta Content Publishing API)

This triples your distribution with zero extra effort.
"""

import os
import json
import time
import logging
import requests
from pathlib import Path
from typing import Optional

logger = logging.getLogger("RevenueMachine.CrossPoster")


class CrossPoster:
    def __init__(self, config: dict):
        self.config = config
        self.ig_access_token = config.get("INSTAGRAM_ACCESS_TOKEN", "")
        self.ig_business_account_id = config.get("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")
        self.tiktok_access_token = config.get("TIKTOK_ACCESS_TOKEN", "")

    def post_all(self, video_path: Path, caption: str, platforms: list) -> dict:
        """
        Posts to all specified platforms.
        Returns dict of {platform: result}.
        """
        results = {}

        for platform in platforms:
            try:
                if platform == "tiktok":
                    results["tiktok"] = self._post_tiktok(video_path, caption)
                elif platform == "instagram_reels":
                    results["instagram_reels"] = self._post_instagram_reels(video_path, caption)
                elif platform == "facebook":
                    results["facebook"] = self._post_facebook_reels(video_path, caption)
            except Exception as e:
                results[platform] = {"success": False, "error": str(e)}

        return results

    # ────────────────────────────────────────────
    #  TIKTOK
    # ────────────────────────────────────────────
    def _post_tiktok(self, video_path: Path, caption: str) -> dict:
        """
        Posts to TikTok via Content Posting API.
        Requires TikTok Developer App with video.publish scope.

        Setup:
        1. Create app at developers.tiktok.com
        2. Request video.publish permission
        3. Get access token via OAuth
        """
        if not self.tiktok_access_token:
            return {"success": False, "error": "TikTok access token not configured"}

        try:
            # Step 1: Initialize upload
            init_resp = requests.post(
                "https://open.tiktokapis.com/v2/post/publish/video/init/",
                headers={
                    "Authorization": f"Bearer {self.tiktok_access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "post_info": {
                        "title": caption[:150],
                        "privacy_level": "SELF_ONLY",  # Change to PUBLIC_TO_EVERYONE when ready
                        "disable_duet": False,
                        "disable_comment": False,
                        "disable_stitch": False,
                        "video_cover_timestamp_ms": 1000,
                    },
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": video_path.stat().st_size,
                        "chunk_size": 10_000_000,  # 10MB chunks
                        "total_chunk_count": 1,
                    },
                },
            )

            if init_resp.status_code != 200:
                return {"success": False, "error": f"Init failed: {init_resp.text[:200]}"}

            init_data = init_resp.json().get("data", {})
            upload_url = init_data.get("upload_url")
            publish_id = init_data.get("publish_id")

            if not upload_url:
                return {"success": False, "error": "No upload URL received"}

            # Step 2: Upload video binary
            with open(video_path, "rb") as f:
                video_data = f.read()

            file_size = len(video_data)
            upload_resp = requests.put(
                upload_url,
                headers={
                    "Content-Range": f"bytes 0-{file_size-1}/{file_size}",
                    "Content-Type": "video/mp4",
                },
                data=video_data,
                timeout=120,
            )

            if upload_resp.status_code not in [200, 206]:
                return {"success": False, "error": f"Upload failed: {upload_resp.status_code}"}

            logger.info(f"  ✅ TikTok: Uploaded (publish_id: {publish_id})")
            return {
                "success": True,
                "publish_id": publish_id,
                "url": f"https://tiktok.com/@rohanshamis/video/{publish_id}",
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    # ────────────────────────────────────────────
    #  INSTAGRAM REELS
    # ────────────────────────────────────────────
    def _post_instagram_reels(self, video_path: Path, caption: str) -> dict:
        """
        Posts a Reel via Meta Content Publishing API.

        Setup:
        1. Create Meta App at developers.facebook.com
        2. Connect Instagram Business Account
        3. Get long-lived access token

        Note: Video must be hosted on a public URL.
        We use a free Cloudflare Workers endpoint or S3 pre-signed URL.
        """
        if not self.ig_access_token or not self.ig_business_account_id:
            return {"success": False, "error": "Instagram credentials not configured"}

        try:
            # Step 1: Upload video to temporary public URL
            video_url = self._upload_to_temp_host(video_path)
            if not video_url:
                return {"success": False, "error": "Failed to get public video URL"}

            # Step 2: Create media container
            create_resp = requests.post(
                f"https://graph.facebook.com/v18.0/{self.ig_business_account_id}/media",
                params={
                    "access_token": self.ig_access_token,
                    "media_type": "REELS",
                    "video_url": video_url,
                    "caption": caption[:2200],
                    "share_to_feed": "true",
                },
            )

            if create_resp.status_code != 200:
                return {"success": False, "error": f"Container creation failed: {create_resp.text[:200]}"}

            container_id = create_resp.json().get("id")

            # Step 3: Wait for processing (usually 30-60 seconds)
            status = self._wait_for_ig_processing(container_id)
            if status != "FINISHED":
                return {"success": False, "error": f"Processing failed with status: {status}"}

            # Step 4: Publish
            publish_resp = requests.post(
                f"https://graph.facebook.com/v18.0/{self.ig_business_account_id}/media_publish",
                params={
                    "access_token": self.ig_access_token,
                    "creation_id": container_id,
                },
            )

            if publish_resp.status_code == 200:
                media_id = publish_resp.json().get("id")
                logger.info(f"  ✅ Instagram: Published (id: {media_id})")
                return {"success": True, "media_id": media_id}
            else:
                return {"success": False, "error": f"Publish failed: {publish_resp.text[:200]}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def _wait_for_ig_processing(self, container_id: str, max_wait: int = 120) -> str:
        """Polls Instagram until video is processed."""
        for _ in range(max_wait // 10):
            time.sleep(10)
            resp = requests.get(
                f"https://graph.facebook.com/v18.0/{container_id}",
                params={
                    "fields": "status_code",
                    "access_token": self.ig_access_token,
                },
            )
            status = resp.json().get("status_code", "")
            logger.info(f"  Instagram processing status: {status}")
            if status == "FINISHED":
                return "FINISHED"
            elif status == "ERROR":
                return "ERROR"
        return "TIMEOUT"

    def _upload_to_temp_host(self, video_path: Path) -> Optional[str]:
        """
        Uploads video to a temporary public URL.
        Options:
        1. Your own VPS/S3 bucket
        2. Cloudinary free tier
        3. Catbox.moe (free, no account needed)
        """
        try:
            # Option: Catbox.moe (anonymous, free, 200MB limit)
            with open(video_path, "rb") as f:
                resp = requests.post(
                    "https://catbox.moe/user/api.php",
                    data={"reqtype": "fileupload"},
                    files={"fileToUpload": f},
                    timeout=120,
                )
            if resp.status_code == 200 and "catbox.moe" in resp.text:
                return resp.text.strip()
        except Exception as e:
            logger.warning(f"Catbox upload failed: {e}")

        return None

    def _post_facebook_reels(self, video_path: Path, caption: str) -> dict:
        """Facebook Reels posting (uses same Meta API)."""
        # Similar to Instagram, but uses Page ID
        return {"success": False, "error": "Facebook Reels not yet configured"}
