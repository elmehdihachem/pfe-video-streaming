import boto3
import os
import uuid

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


# ─────────────────────────────────────────────────────────────────────────────
# NOUVEAU — Générer une Presigned URL pour upload direct depuis le navigateur
# ─────────────────────────────────────────────────────────────────────────────
def generate_presigned_url(filename: str, content_type: str) -> dict:
    client = get_r2_client()
    bucket = os.getenv("R2_BUCKET_NAME")

    # Générer un nom unique pour éviter les conflits
    # ex: "raw_uploads/a1b2c3d4-e5f6-7890.mp4"
    extension = filename.rsplit(".", 1)[-1] if "." in filename else "mp4"
    r2_key    = f"raw_uploads/{uuid.uuid4()}.{extension}"

    # generate_presigned_url → génère une URL signée pour une opération PUT
    # "put_object" → le navigateur va écrire (uploader) le fichier
    # ExpiresIn   → l'URL expire après 3600 secondes (1 heure)
    # Params      → on précise le bucket, la clé et le content-type
    #               le navigateur DOIT envoyer le même content-type dans sa requête PUT
    presigned_url = client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket":      bucket,
            "Key":         r2_key,
            "ContentType": content_type,
        },
        ExpiresIn=3600  # 1 heure
    )

    return {
        "presigned_url": presigned_url,
        "r2_key":        r2_key
    }


# ─────────────────────────────────────────────────────────────────────────────
# INCHANGÉ — Upload d'un dossier entier vers R2 (utilisé après encodage FFmpeg)
# ─────────────────────────────────────────────────────────────────────────────
def upload_folder_to_r2(local_folder, video_folder_name):
    client   = get_r2_client()
    bucket   = os.getenv("R2_BUCKET_NAME")
    base_url = os.getenv("R2_PUBLIC_DOMAIN", os.getenv("R2_ENDPOINT"))

    playlist_url = None

    for filename in os.listdir(local_folder):
        file_path = os.path.join(local_folder, filename)
        if not os.path.isfile(file_path):
            continue

        # Chemin dans R2 : videos/uuid/720p/video.m3u8
        r2_key = f"videos/{video_folder_name}/{filename}"
        client.upload_file(file_path, bucket, r2_key)

        if filename.endswith(".m3u8"):
            playlist_url = f"{base_url}/{r2_key}"

    return playlist_url


# ─────────────────────────────────────────────────────────────────────────────
# NOUVEAU — Supprimer la vidéo brute de R2 après encodage
# r2_key → chemin de la vidéo brute ex: "raw_uploads/uuid.mp4"
# ─────────────────────────────────────────────────────────────────────────────
def delete_raw_video(r2_key: str):
    client = get_r2_client()
    bucket = os.getenv("R2_BUCKET_NAME")

    try:
        client.delete_object(Bucket=bucket, Key=r2_key)
        print(f"✅ Vidéo brute supprimée de R2 : {r2_key}")
    except Exception as e:
        print(f"❌ Erreur suppression vidéo brute R2 : {e}")


# ─────────────────────────────────────────────────────────────────────────────
# MODIFIÉ — Supprimer les fichiers encodés HLS de R2
# ─────────────────────────────────────────────────────────────────────────────
def delete_encoded_videos(url_720p: str, url_1080p: str):
    client   = get_r2_client()
    bucket   = os.getenv("R2_BUCKET_NAME")
    base_url = os.getenv("R2_PUBLIC_DOMAIN")

    for url in [url_720p, url_1080p]:
        if not url:
            continue

        try:
            # Extraire le prefix du dossier depuis l'URL
            r2_key = url.replace(f"{base_url}/", "")        # "videos/uuid/720p/video.m3u8"
            prefix = r2_key.rsplit("/", 1)[0] + "/"         # "videos/uuid/720p/"

            # Lister tous les fichiers dans ce dossier et les supprimer
            response = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
            for obj in response.get("Contents", []):
                client.delete_object(Bucket=bucket, Key=obj["Key"])
                print(f"✅ Supprimé R2 : {obj['Key']}")

        except Exception as e:
            print(f"❌ Erreur suppression R2 ({url}) : {e}")