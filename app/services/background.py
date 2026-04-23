import os
import shutil
import asyncio
import httpx

from database.db import SessionLocal
from models.video import Video, VideoStatus
from services.r2 import upload_folder_to_r2, delete_raw_upload

ENCODED_DIR = "/tmp"

async def encode_quality(input_path: str, output_dir: str, resolution: str, bitrate: str):
    os.makedirs(output_dir, exist_ok=True)

    playlist_path = os.path.join(output_dir, "video.m3u8")
    segment_path  = os.path.join(output_dir, "chunk_%03d.ts")

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", f"scale={resolution}",
        "-b:v", bitrate,
        "-hls_time", "3",
        "-hls_list_size", "0",
        "-hls_segment_filename", segment_path,
        "-f", "hls",
        playlist_path
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        raise Exception(f"FFmpeg error ({resolution}):\n{stderr.decode()}")


async def process_video(video_id: int, temp_path: str, video_folder_name: str):
    db = SessionLocal()
    # Dossiers par qualité
    dir_720p = os.path.join(ENCODED_DIR, video_folder_name, "720p")
    dir_1080p = os.path.join(ENCODED_DIR, video_folder_name, "1080p")

    #video_output_dir = os.path.join(ENCODED_DIR, video_folder_name)

    try:
        video = db.query(Video).filter(Video.id == video_id).first()

        # ─────────────────────────────────────────
        # Étape 1 — Encodage 720p ET 1080p en parallèle
        # ─────────────────────────────────────────
        video.status = VideoStatus.ENCODING
        db.commit()
        await asyncio.gather(
            encode_quality(temp_path, dir_720p, "1280:720", "2500k"),
            encode_quality(temp_path, dir_1080p, "1920:1080", "5000k"),
        )

        # ─────────────────────────────────────────
        # Étape 2 — Upload vers R2
        # ─────────────────────────────────────────
        video.status = VideoStatus.UPLOADING
        db.commit()

        #playlist_url = upload_folder_to_r2(video_output_dir, video_folder_name)
        url_720p = upload_folder_to_r2(dir_720p, f"{video_folder_name}/720p")
        url_1080p = upload_folder_to_r2(dir_1080p, f"{video_folder_name}/1080p")

        # ─────────────────────────────────────────
        # Étape 3 — Mise à jour MySQL
        # ─────────────────────────────────────────
        video.playlist_url = url_720p   # URL par défaut = 720p
        video.playlist_url_1080 = url_1080p  # 1080p
        video.status       = VideoStatus.DONE
        db.commit()

        # ─────────────────────────────────────────
        # Étape 4 — Webhook ver Flask
        # ─────────────────────────────────────────
        webhook_url = os.getenv("LMS_WEBHOOK_URL", "http://flask:5000/webhooks/video-ready")
        #if webhook_url:
        async with httpx.AsyncClient() as client:
            await client.post(webhook_url, json={
                "video_id":     video_id,
                "url_720p":    url_720p,
                "url_1080p":   url_1080p,
            }, timeout=10)

    except Exception as e:
        video = db.query(Video).filter(Video.id == video_id).first()
        if video:
            video.status = VideoStatus.ERROR
            db.commit()
        print(f"Erreur process_video: {e}")

    finally:
        # Nettoyage /tmp/
        if os.path.exists(temp_path):
            os.remove(temp_path)
            base_dir = os.path.join(ENCODED_DIR, video_folder_name)
        if os.path.exists(base_dir):
            shutil.rmtree(base_dir, ignore_errors=True)
        db.close()