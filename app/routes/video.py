import os
from services.r2 import get_r2_client
from PIL import Image
import io
import uuid
import shutil
from fastapi import APIRouter, UploadFile, File, Depends, BackgroundTasks
from sqlalchemy.orm import Session

from database.db import get_db
from models.video import Video, VideoStatus
from services.background import process_video

router = APIRouter()

UPLOAD_DIR = "/app/uploads"


@router.post("/upload")
async def upload_video(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db)
):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    temp_path = os.path.join(UPLOAD_DIR, file.filename)
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    video_folder_name = str(uuid.uuid4())

    video = Video(nom=file.filename, status=VideoStatus.PENDING)
    db.add(video)
    db.commit()
    db.refresh(video)

    background_tasks.add_task(process_video, video.id, temp_path, video_folder_name)

    return {
        "message": "Upload reçu, encodage en cours en arrière-plan",
        "video_id": video.id,
        "video_folder": video_folder_name,
        "status": video.status
    }


@router.post("/upload/thumbnail")
async def upload_thumbnail(file: UploadFile = File(...)):
    contents = await file.read()
    img = Image.open(io.BytesIO(contents))

    img = img.convert("RGB")
    img = img.resize((800, 600), Image.LANCZOS)

    output = io.BytesIO()
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
    r2_key   = f"thumbnails/{thumbnail_id}.jpg"
    bucket   = os.getenv("R2_BUCKET_NAME")
    base_url = os.getenv("R2_PUBLIC_DOMAIN")

    get_r2_client().upload_fileobj(
        output, bucket, r2_key,
        ExtraArgs={"ContentType": "image/jpeg"}
    )

    thumbnail_url = f"{base_url}/{r2_key}"
    return {"thumbnail_url": thumbnail_url}


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


# ✅ Suppression vidéo + fichiers R2
@router.delete("/video/{video_id}/files")
def delete_video_files(video_id: int, db: Session = Depends(get_db)):
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        return {"error": "Vidéo non trouvée"}

    try:
        client = get_r2_client()
        bucket = os.getenv("R2_BUCKET_NAME")

        if video.playlist_url:
            # ex URL: https://pub.../videos/uuid/720p/video.m3u8
            parts       = video.playlist_url.split("/")
            uuid_index  = parts.index("videos") + 1
            folder_name = parts[uuid_index]

            # Supprime 720p et 1080p
            for quality in ["720p", "1080p"]:
                prefix   = f"videos/{folder_name}/{quality}/"
                response = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
                for obj in response.get("Contents", []):
                    client.delete_object(Bucket=bucket, Key=obj["Key"])
                    print(f"Supprimé R2: {obj['Key']}")

    except Exception as e:
        print(f"Erreur suppression R2: {e}")

    # Supprime de MySQL
    db.delete(video)
    db.commit()

    return {"message": "Vidéo et fichiers supprimés"}