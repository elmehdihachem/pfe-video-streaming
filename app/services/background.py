import os
import json
import uuid
import shutil
import asyncio
import httpx


import redis

from database.db import SessionLocal
from models.video import Video, VideoStatus
from services.r2 import upload_folder_to_r2, delete_raw_video

ENCODED_DIR = "/tmp"


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI et Flask partagent le même Redis Upstash
# ─────────────────────────────────────────────────────────────────────────────
redis_client = redis.from_url(
    os.getenv("UPSTASH_REDIS_URL"),
    decode_responses=True
)


# ─────────────────────────────────────────────────────────────────────────────
#  — Encodage FFmpeg pour une qualité donnée
# ─────────────────────────────────────────────────────────────────────────────
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
        "-crf", "20",
        "-maxrate", maxrate,
        "-bufsize", bufsize,
        "-preset", "slow",
        "-g", "48",
        "-keyint_min", "48",

        # ── Audio ──
        "-c:a", "aac",
        "-b:a", "192k",
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


# ─────────────────────────────────────────────────────────────────────────────
# NOUVEAU — Télécharger la vidéo brute depuis R2
# ─────────────────────────────────────────────────────────────────────────────
async def download_video_from_r2(video_url: str, local_path: str):
    async with httpx.AsyncClient() as client:
        # stream=True → télécharge par morceaux pour ne pas charger tout en mémoire
        async with client.stream("GET", video_url) as response:
            response.raise_for_status()
            with open(local_path, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    f.write(chunk)
    print(f"✅ Vidéo téléchargée depuis R2 : {local_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  — Traitement complet d'une vidéo
#
# AVANT : recevait video_id + temp_path (fichier déjà sur disque) + video_folder_name
#         appelé directement par BackgroundTasks FastAPI depuis /upload
#
# MAINTENANT : recevait course_id + video_url (URL R2) depuis Redis
#   Étape 1 → Télécharge la vidéo depuis R2
#   Étape 2 → Encode en 720p + 1080p (inchangé, asyncio.gather)
#   Étape 3 → Upload HLS sur R2 (inchangé)
#   Étape 4 → Supprime la vidéo brute de R2 (NOUVEAU)
#   Étape 5 → Écrit le résultat dans Redis (NOUVEAU, remplace le webhook)
#   Étape 6 → Appelle le webhook Flask en backup (inchangé)
#   Étape 7 → Cleanup local (inchangé)
# ─────────────────────────────────────────────────────────────────────────────
async def process_video(course_id: int, video_url: str):

    # ── Générer un dossier unique pour cette vidéo ──
    # INCHANGÉ — même logique qu'avant, juste déplacée ici
    # car la route /upload qui le générait avant est supprimée
    video_folder_name = str(uuid.uuid4())
    temp_path         = os.path.join(ENCODED_DIR, f"{video_folder_name}_raw.mp4")
    dir_720p          = os.path.join(ENCODED_DIR, video_folder_name, "720p")
    dir_1080p         = os.path.join(ENCODED_DIR, video_folder_name, "1080p")
    base_dir          = os.path.join(ENCODED_DIR, video_folder_name)

    # Extraire le r2_key depuis l'URL pour pouvoir supprimer la vidéo brute après
    # ex: "https://pub-xxx.r2.dev/raw_uploads/uuid.mp4" → "raw_uploads/uuid.mp4"
    base_url = os.getenv("R2_PUBLIC_DOMAIN")
    r2_key   = video_url.replace(f"{base_url}/", "")

    try:
        # ─────────────────────────────────────────────────────────────────────
        # Étape 1 — Télécharger la vidéo brute depuis R2
        # NOUVEAU — dans l'ancien système cette étape n'existait pas
        # ─────────────────────────────────────────────────────────────────────
        print(f"⬇️  Téléchargement vidéo depuis R2 : {video_url}")
        await download_video_from_r2(video_url, temp_path)

        # ─────────────────────────────────────────────────────────────────────
        # Étape 2 — Encodage 720p ET 1080p en parallèle
        # INCHANGÉ — asyncio.gather encode les deux qualités en même temps
        # ─────────────────────────────────────────────────────────────────────
        print(f"🎬 Encodage 720p + 1080p en parallèle...")
        await asyncio.gather(
            encode_quality(
                temp_path, dir_720p,
                width=1280, height=720,
                bitrate="2500k",
                maxrate="3000k",
                bufsize="6000k"
            ),
            encode_quality(
                temp_path, dir_1080p,
                width=1920, height=1080,
                bitrate="5000k",
                maxrate="6000k",
                bufsize="12000k"
            ),
        )

        # ─────────────────────────────────────────────────────────────────────
        # Étape 3 — Upload HLS encodé vers R2
        # INCHANGÉ — upload les fichiers .m3u8 + .ts dans videos/uuid/720p et 1080p
        # MODIFIÉ  — upload dans un thread séparé pour ne pas bloquer l'event loop
        # ─────────────────────────────────────────────────────────────────────
        print(f"⬆️  Upload HLS vers R2...")
        url_720p  = await asyncio.to_thread(upload_folder_to_r2, dir_720p,  f"{video_folder_name}/720p")
        url_1080p = await asyncio.to_thread(upload_folder_to_r2, dir_1080p, f"{video_folder_name}/1080p")

        # ─────────────────────────────────────────────────────────────────────
        # Étape 4 — Supprimer la vidéo brute de R2
        # NOUVEAU — la vidéo brute dans raw_uploads/ n'est plus nécessaire
        #           on la supprime pour économiser de l'espace sur R2
        # ─────────────────────────────────────────────────────────────────────
        print(f"🗑️  Suppression vidéo brute R2 : {r2_key}")
        await asyncio.to_thread(delete_raw_video, r2_key)

        # ─────────────────────────────────────────────────────────────────────
        # Étape 5 — Écrire le résultat dans Redis
        # NOUVEAU — remplace la mise à jour MySQL directe
        #
        # FastAPI écrit dans Redis la clé "status:{course_id}"
        # Flask lit cette clé toutes les 10s via /status/<course_id>
        # et met à jour MySQL quand il la trouve
        #
        # On utilise redis SET avec expiration 1 heure (3600s)
        # au cas où Flask ne lirait jamais la clé (sécurité)
        # ─────────────────────────────────────────────────────────────────────
        print(f"📨 Publication résultat dans Redis pour course_id={course_id}")
        redis_client.set(
            f"status:{course_id}",
            json.dumps({
                "status":   "DONE",
                "url_720p":  url_720p,
                "url_1080p": url_1080p,
            }),
            ex=3600  # expire après 1 heure si Flask ne lit pas
        )

        # ─────────────────────────────────────────────────────────────────────
        # Étape 6 — Webhook Flask en backup
        # INCHANGÉ — au cas où Redis aurait un problème
        # MODIFIÉ  — envoie course_id au lieu de video_id
        # ─────────────────────────────────────────────────────────────────────
        webhook_url = os.getenv("LMS_WEBHOOK_URL", "http://flask:5000/webhooks/video-ready")
        async with httpx.AsyncClient() as client:
            await client.post(webhook_url, json={
                "course_id": course_id,
                "url_720p":  url_720p,
                "url_1080p": url_1080p,
            }, timeout=10)

    except Exception as e:
        # ── En cas d'erreur → écrire ERROR dans Redis ──
        # Flask lira cette clé et mettra le cours en ERROR dans MySQL
        print(f"❌ Erreur process_video course_id={course_id} : {e}")
        redis_client.set(
            f"status:{course_id}",
            json.dumps({"status": "ERROR"}),
            ex=3600
        )

    finally:
        # ─────────────────────────────────────────────────────────────────────
        # Étape 7 — Cleanup local
        # INCHANGÉ — supprime les fichiers temporaires locaux
        # ─────────────────────────────────────────────────────────────────────
        if os.path.exists(temp_path):
            os.remove(temp_path)
        if os.path.exists(base_dir):
            shutil.rmtree(base_dir, ignore_errors=True)
        print(f"🧹 Cleanup local terminé pour course_id={course_id}")


# ─────────────────────────────────────────────────────────────────────────────
# NOUVEAU — Écouter Redis et traiter les vidéos

# IMPORTANT : on utilise un thread séparé car redis pubsub est bloquant
# ─────────────────────────────────────────────────────────────────────────────
async def listen_redis():
    print("👂 FastAPI écoute Redis canal 'video_uploaded'...")

    # ── on_message est appelée dans un thread séparé ──
    # pubsub.listen() est bloquant donc on le met dans un thread
    # pour ne pas bloquer le démarrage de FastAPI
    def blocking_listen():
        pubsub = redis_client.pubsub()
        pubsub.subscribe("video_uploaded")

        for message in pubsub.listen():
            if message["type"] != "message":
                continue

            try:
                data      = json.loads(message["data"])
                course_id = data["course_id"]
                video_url = data["video_url"]

                print(f"📥 Événement reçu : course_id={course_id}, url={video_url}")

                # Lancer process_video dans l'event loop principal
                asyncio.run_coroutine_threadsafe(
                    process_video(course_id, video_url),
                    loop
                )

            except Exception as e:
                print(f"❌ Erreur traitement événement Redis : {e}")

    # Récupérer l'event loop principal
    loop = asyncio.get_event_loop()

    # Exécuter blocking_listen dans un thread séparé
    # pour ne pas bloquer FastAPI
    await asyncio.to_thread(blocking_listen)