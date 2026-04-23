import os
import uuid
import shutil
from fastapi import APIRouter, UploadFile, File, Depends, BackgroundTasks
from sqlalchemy.orm import Session

from database.db import get_db
from models.video import Video, VideoStatus
#from services.encoder import encode_video
#from services.r2 import upload_folder_to_r2
from services.background import process_video


router = APIRouter()

UPLOAD_DIR = "/app/uploads"
#ENCODED_DIR = "/app/uploads/encoded"

@router.post("/upload")
async def upload_video(file: UploadFile = File(...),background_tasks: BackgroundTasks = BackgroundTasks(),
                       db: Session = Depends(get_db)):

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    temp_path = os.path.join(UPLOAD_DIR, file.filename)
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # ✅ UUID généré ici, transmis à la background task
    video_folder_name = str(uuid.uuid4())

    video = Video(nom=file.filename, status=VideoStatus.PENDING)
    db.add(video)
    db.commit()
    db.refresh(video)
#à
    #playlist_url = None

    # Lance le background task — ne bloque pas la réponse
    background_tasks.add_task(process_video, video.id, temp_path, video_folder_name)

    return {
        "message": "Upload reçu, encodage en cours en arrière-plan",
        "video_id": video.id,
        "video_folder": video_folder_name,
        "status": video.status
    }

    #try:
       # video.status = VideoStatus.ENCODING
        #db.commit()

        ## encode_video retourne maintenant (playlist_path, video_folder_name)
        #playlist_path, video_folder_name = encode_video(temp_path, ENCODED_DIR)
        #video_output_dir = os.path.dirname(playlist_path)

        #video.status = VideoStatus.UPLOADING
       # db.commit()

        # Upload tout le dossier vers R2
        #playlist_url = upload_folder_to_r2(video_output_dir, video_folder_name)

        #video.playlist_url = playlist_url
        #video.status = VideoStatus.DONE
        #db.commit()

    #except Exception as e:
        #video.status = VideoStatus.ERROR
        #db.commit()
        #return {"error": str(e)}

    #finally:
        # Nettoyage du dossier encodé de cette vidéo
        #video_output_dir_to_clean = os.path.join(ENCODED_DIR, video_folder_name) \
            #if 'video_folder_name' in locals() else ENCODED_DIR
        #if os.path.exists(video_output_dir_to_clean):
            #shutil.rmtree(video_output_dir_to_clean, ignore_errors=True)
        #if os.path.exists(temp_path):
           # os.remove(temp_path)

   # return {
        #"message": "Vidéo uploadée avec succès !",
        #"video_id": video.id,
       # "playlist_url": playlist_url,
        #"status": video.status
   # }



@router.get("/video/{video_id}")
def get_video(video_id: int, db: Session = Depends(get_db)):

    video = db.query(Video).filter(Video.id == video_id).first()

    if not video:
        return {"error": "Vidéo non trouvée"}

    return {
        "id": video.id,
        "nom": video.nom,
        #"playlist_url": video.playlist_url,
        "status": video.status,
        "created_at": video.created_at,
        "playlist_720p": video.playlist_url,
        "playlist_1080p": video.playlist_url_1080,
    }
