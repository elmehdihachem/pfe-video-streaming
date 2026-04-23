import boto3
import os

from dotenv import load_dotenv
load_dotenv()


def get_r2_client():
    client = boto3.client(
        "s3",
        endpoint_url=os.getenv("R2_ENDPOINT"),
        aws_access_key_id=os.getenv("R2_ACCESS_KEY"),
        aws_secret_access_key=os.getenv("R2_SECRET_KEY"),
        region_name="auto"
    )
    return client



    #docker fast api streaming -- document
# -- implementation
# ---- diagramme architecture microservices + reddis (upload - webhook -



def upload_folder_to_r2(local_folder, video_folder_name):
    """
    Upload tous les fichiers d'un dossier local vers R2
    dans le chemin : videos/{video_folder_name}/
    Retourne l'URL de la playlist .m3u8
    """
    client = get_r2_client()
    bucket = os.getenv("R2_BUCKET_NAME")
    base_url = os.getenv("R2_PUBLIC_DOMAIN", os.getenv("R2_ENDPOINT"))

    playlist_url = None

    for filename in os.listdir(local_folder):
        file_path = os.path.join(local_folder, filename)
        if not os.path.isfile(file_path):
            continue

        # Clé R2 : videos/uuid/video.m3u8  ou  videos/uuid/chunk_001.ts
        r2_key = f"videos/{video_folder_name}/{filename}"
        client.upload_file(file_path, bucket, r2_key)

        if filename.endswith(".m3u8"):
            playlist_url = f"{base_url}/{r2_key}"
    return playlist_url
def delete_raw_upload(job_id: str):
    client = get_r2_client()
    bucket = os.getenv("R2_BUCKET_NAME")
    client.delete_object(Bucket=bucket, Key=f"raw_uploads/{job_id}.mp4")
