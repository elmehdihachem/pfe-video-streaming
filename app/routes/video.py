import os
import uuid
import shutil
import io

from PIL import Image
from fastapi import APIRouter, UploadFile, File, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database.db import get_db
from models.video import Video, VideoStatus
from services.background import process_video
from services.r2 import (
    get_r2_client,
    generate_presigned_url,
    delete_encoded_videos
)

router = APIRouter()

UPLOAD_DIR = "/app/uploads"


# ─────────────────────────────────────────────────────────────────────────────

#  le navigateur uploade directement sur R2 via presigned URL
#              FastAPI écoute Redis et traite la vidéo quand elle est prête
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# NOUVEAU — Générer une Presigned URL pour upload direct vers R2
# ─────────────────────────────────────────────────────────────────────────────

# Modèle Pydantic pour valider le body de la requête
class PresignedUrlRequest(BaseModel):
    filename:     str
    content_type: str


@router.post("/presigned-url")
def get_presigned_url(body: PresignedUrlRequest):
    # Appelle la fonction dans r2.py qui génère l'URL signée
    result = generate_presigned_url(
        filename     = body.filename,
        content_type = body.content_type
    )

    # Retourne au Flask :
    #   presigned_url → URL signée pour uploader directement sur R2 (expire 1h)
    #   r2_key        → chemin dans R2 ex: "raw_uploads/uuid.mp4"
    return {
        "presigned_url": result["presigned_url"],
        "r2_key":        result["r2_key"]
    }


# ─────────────────────────────────────────────────────────────────────────────
#  — Upload thumbnail
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/upload/thumbnail")
async def upload_thumbnail(file: UploadFile = File(...)):
    contents = await file.read()
    img      = Image.open(io.BytesIO(contents))

    img = img.convert("RGB")
    img = img.resize((800, 600), Image.LANCZOS)

    # Compression JPEG jusqu'à 150Ko max
    output  = io.BytesIO()
    quality = 85
    while True:
        output.seek(0)
        output.truncate()
        img.save(output, format="JPEG", quality=quality)
        if output.tell() <= 150 * 1024 or quality <= 20:
            break
        quality -= 5

    output.seek(0)
    thumbnail_id = str(uuid.uuid4())
    r2_key       = f"thumbnails/{thumbnail_id}.jpg"
    bucket       = os.getenv("R2_BUCKET_NAME")
    base_url     = os.getenv("R2_PUBLIC_DOMAIN")

    get_r2_client().upload_fileobj(
        output, bucket, r2_key,
        ExtraArgs={"ContentType": "image/jpeg"}
    )

    thumbnail_url = f"{base_url}/{r2_key}"
    return {"thumbnail_url": thumbnail_url}


# ─────────────────────────────────────────────────────────────────────────────
#  — Récupérer le statut d'une vidéo
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/video/{video_id}")
def get_video(video_id: int, db: Session = Depends(get_db)):
    video = db.query(Video).filter(Video.id == video_id).first()

    if not video:
        return {"error": "Vidéo non trouvée"}

    return {
        "id":           video.id,
        "nom":          video.nom,
        "status":       video.status,
        "created_at":   video.created_at,
        "playlist_720p":  video.playlist_url,
        "playlist_1080p": video.playlist_url_1080,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  — Supprimer les fichiers encodés de R2
# ─────────────────────────────────────────────────────────────────────────────

# Modèle Pydantic pour valider le body de la requête
class DeleteFilesRequest(BaseModel):
    url_720p:  str
    url_1080p: str


@router.delete("/video/files")
def delete_video_files(body: DeleteFilesRequest):
    # Appelle la fonction dans r2.py qui supprime les fichiers HLS sur R2
    delete_encoded_videos(
        url_720p  = body.url_720p,
        url_1080p = body.url_1080p
    )
    return {"message": "Fichiers supprimés"}