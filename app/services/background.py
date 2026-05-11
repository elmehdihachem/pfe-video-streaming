import os
import shutil
import asyncio
import httpx

from database.db import SessionLocal
from models.video import Video, VideoStatus
from services.r2 import upload_folder_to_r2

ENCODED_DIR = "/tmp"

async def encode_quality(input_path: str, output_dir: str, width: int, height: int, bitrate: str, maxrate: str, bufsize: str):
    os.makedirs(output_dir, exist_ok=True)

    playlist_path = os.path.join(output_dir, "video.m3u8")
    segment_path  = os.path.join(output_dir, "chunk_%03d.ts")

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,

        # ── Video ──
        "-c:v", "libx264",
        "-profile:v", "high",
        "-level", "4.1",
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",

        # ✅ CRF seul — qualité constante sans conflit bitrate
        "-crf", "20",  # 18-23 = excellente qualité
        "-maxrate", maxrate,  # plafond bitrate
        "-bufsize", bufsize,  # buffer

        "-preset", "slow",  # ✅ slow = meilleure qualité que medium
        "-g", "48",
        "-keyint_min", "48",  # ✅ keyframes réguliers pour HLS

        # ── Audio ──
        "-c:a", "aac",
        "-b:a", "192k",  # ✅ 192k au lieu de 128k
        "-ar", "48000",
        "-ac", "2",

        # ── HLS ──
        "-hls_time", "4",
        "-hls_list_size", "0",
        "-hls_segment_filename", segment_path,
        "-hls_flags", "independent_segments",
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
        raise Exception(f"FFmpeg error ({width}x{height}):\n{stderr.decode()}")


async def process_video(video_id: int, temp_path: str, video_folder_name: str):
    db = SessionLocal()

    dir_720p  = os.path.join(ENCODED_DIR, video_folder_name, "720p")
    dir_1080p = os.path.join(ENCODED_DIR, video_folder_name, "1080p")
    base_dir  = os.path.join(ENCODED_DIR, video_folder_name)

    try:
        video = db.query(Video).filter(Video.id == video_id).first()

        # ─────────────────────────────────────────
        # Étape 1 — Encodage 720p ET 1080p en parallèle
        # ─────────────────────────────────────────
        video.status = VideoStatus.ENCODING
        db.commit()

        await asyncio.gather(
            encode_quality(
                temp_path, dir_720p,
                width=1280, height=720,
                bitrate="2500k",
                maxrate="3000k",  # ✅ plus de marge
                bufsize="6000k"  # ✅ bufsize = 2x maxrate
            ),
            encode_quality(
                temp_path, dir_1080p,
                width=1920, height=1080,
                bitrate="5000k",
                maxrate="6000k",  # ✅ plus de marge
                bufsize="12000k"  # ✅ bufsize = 2x maxrate
            ),
        )

        # ─────────────────────────────────────────
        # Étape 2 — Upload vers R2
        # ─────────────────────────────────────────
        video.status = VideoStatus.UPLOADING
        db.commit()

        url_720p  = upload_folder_to_r2(dir_720p,  f"{video_folder_name}/720p")
        url_1080p = upload_folder_to_r2(dir_1080p, f"{video_folder_name}/1080p")

        # ─────────────────────────────────────────
        # Étape 3 — Mise à jour MySQL
        # ─────────────────────────────────────────
        video.playlist_url      = url_720p
        video.playlist_url_1080 = url_1080p
        video.status            = VideoStatus.DONE
        db.commit()

        # ─────────────────────────────────────────
        # Étape 4 — Webhook vers Flask
        # ─────────────────────────────────────────
        webhook_url = os.getenv("LMS_WEBHOOK_URL", "http://flask:5000/webhooks/video-ready")
        async with httpx.AsyncClient() as client:
            await client.post(webhook_url, json={
                "video_id":  video_id,
                "url_720p":  url_720p,
                "url_1080p": url_1080p,
            }, timeout=10)

    except Exception as e:
        video = db.query(Video).filter(Video.id == video_id).first()
        if video:
            video.status = VideoStatus.ERROR
            db.commit()
        print(f"Erreur process_video: {e}")

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        if os.path.exists(base_dir):
            shutil.rmtree(base_dir, ignore_errors=True)
        db.close()