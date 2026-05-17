import os
import json

import redis
import requests
from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from models import db, Course

# ─────────────────────────────────────────────────────────────────────────────
# NOUVEAU — import Redis
# Upstash utilise la librairie "redis" standard, 100% compatible
# Ajoute "redis" dans flask_app/requirements.txt
# ─────────────────────────────────────────────────────────────────────────────



app = Flask(__name__)


app.secret_key = os.getenv("FLASK_SECRET_KEY", "lms_secret_key")

app.config["SQLALCHEMY_DATABASE_URI"] = (
    f"mysql+pymysql://{os.getenv('MYSQL_USER')}:{os.getenv('MYSQL_PASSWORD')}"
    f"@{os.getenv('MYSQL_HOST')}/{os.getenv('MYSQL_DATABASE')}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2GB max

FASTAPI_URL = os.getenv("FASTAPI_URL", "http://api:8000/api/v1")

db.init_app(app)

with app.app_context():
    db.create_all()


# ─────────────────────────────────────────────────────────────────────────────
# NOUVEAU — Connexion Redis via Upstash
# ─────────────────────────────────────────────────────────────────────────────
redis_client = redis.from_url(
    os.getenv("UPSTASH_REDIS_URL"),
    decode_responses=True
)


# ─────────────────────────────────────────
# Page principale — liste des vidéos
# INCHANGÉ
# ─────────────────────────────────────────
@app.route("/")
def index():
    courses = Course.query.order_by(Course.created_at.desc()).all()
    return render_template("index.html", courses=courses)


# ─────────────────────────────────────────────────────────────────────────────
# Page 1 — Titre + Thumbnail
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/upload/info", methods=["GET", "POST"])
def upload_info():
    if request.method == "POST":
        titre     = request.form.get("titre")
        thumbnail = request.files.get("thumbnail")

        thumbnail_url = None

        if thumbnail and thumbnail.filename:
            files    = {"file": (thumbnail.filename, thumbnail.stream, thumbnail.content_type)}
            response = requests.post(f"{FASTAPI_URL}/upload/thumbnail", files=files)
            if response.status_code == 200:
                thumbnail_url = response.json().get("thumbnail_url")

        session["titre"]         = titre
        session["thumbnail_url"] = thumbnail_url

        return redirect(url_for("upload_video"))

    return render_template("upload_info.html")


# ─────────────────────────────────────────
# Page 2 — Upload vidéo avec progression
# ─────────────────────────────────────────
@app.route("/upload/video")
def upload_video():
    titre         = session.get("titre")
    thumbnail_url = session.get("thumbnail_url")
    if not titre:
        return redirect(url_for("upload_info"))
    return render_template("upload_video.html", titre=titre, thumbnail_url=thumbnail_url)


# ─────────────────────────────────────────────────────────────────────────────
# NOUVEAU — Génération de la Presigned URL pour upload direct vers R2
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/presigned-url", methods=["POST"])
def get_presigned_url():
    data         = request.get_json()
    filename     = data.get("filename")      # ex: "cours-python.mp4"
    content_type = data.get("content_type")  # ex: "video/mp4"

    if not filename or not content_type:
        return jsonify({"error": "filename et content_type requis"}), 400

    response = requests.post(
        f"{FASTAPI_URL}/presigned-url",
        json={"filename": filename, "content_type": content_type}
    )

    if response.status_code != 200:
        return jsonify({"error": "Impossible de générer la presigned URL"}), 500

    result = response.json()

    # presigned_url → URL signée pour uploader sur R2 (expire dans 1h)
    # r2_key        → chemin dans R2, ex: "raw_uploads/uuid.mp4"
    return jsonify({
        "presigned_url": result["presigned_url"],
        "r2_key":        result["r2_key"]
    })


# ─────────────────────────────────────────────────────────────────────────────
# NOUVEAU — Confirmer l'upload R2 + publier l'événement dans Redis
# Appelé par le navigateur APRÈS que son upload direct vers R2 est terminé.
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/upload/confirm", methods=["POST"])
def confirm_upload():
    data          = request.get_json()
    titre         = data.get("titre")
    thumbnail_url = data.get("thumbnail_url", "")
    r2_key        = data.get("r2_key")  # ex: "raw_uploads/uuid.mp4"

    if not r2_key or not titre:
        return jsonify({"error": "titre et r2_key requis"}), 400

    # Construire l'URL publique de la vidéo brute sur R2
    base_url  = os.getenv("R2_PUBLIC_DOMAIN")
    video_url = f"{base_url}/{r2_key}"

    # Créer l'entrée dans MySQL
    course = Course(
        titre         = titre,
        thumbnail_url = thumbnail_url or None,
        video_id      = None,
        status        = "PENDING"
    )
    db.session.add(course)
    db.session.commit()

    # Publier l'événement dans Redis
    # FastAPI abonné au canal "video_uploaded" le reçoit instantanément
    redis_client.publish("video_uploaded", json.dumps({
        "course_id": course.id,   # pour savoir quel cours mettre à jour
        "video_url": video_url,   # URL de la vidéo brute à encoder
    }))

    session.pop("titre", None)
    session.pop("thumbnail_url", None)

    return jsonify({
        "message":   "Upload confirmé, encodage lancé !",
        "course_id": course.id,
    })


# ─────────────────────────────────────────────────────────────────────────────
# MODIFIÉ — Webhook reçu depuis FastAPI (garde en BACKUP si Redis KO)
# MODIFIÉ : cherche par course_id (pas video_id)
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/webhooks/video-ready", methods=["POST"])
def webhook_video_ready():
    data      = request.get_json()
    course_id = data.get("course_id")  # MODIFIÉ : course_id au lieu de video_id
    url_720p  = data.get("url_720p")
    url_1080p = data.get("url_1080p")

    course = Course.query.filter_by(id=course_id).first()
    if not course:
        return jsonify({"error": "Course introuvable"}), 404

    course.status         = "DONE"
    course.playlist_720p  = url_720p
    course.playlist_1080p = url_1080p
    db.session.commit()

    return jsonify({"message": "OK"}), 200


# ─────────────────────────────────────────────────────────────────────────────
# MODIFIÉ — Poll statut (appelé en AJAX toutes les 10s)
# MAINTENANT : Flask vérifie Redis d'abord (rapide, décharge FastAPI)
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/status/<int:course_id>")
def check_status(course_id):
    course = Course.query.get_or_404(course_id)

    # Cas 1 : déjà terminé → retourne immédiatement
    if course.status in ["DONE", "ERROR"]:
        return jsonify({
            "status":         course.status,
            "playlist_720p":  course.playlist_720p,
            "playlist_1080p": course.playlist_1080p
        })

    # Cas 2 : vérifie Redis
    # FastAPI écrit : redis_client.set("status:{course_id}", json.dumps({...}))
    redis_status = redis_client.get(f"status:{course_id}")

    if redis_status:
        redis_data = json.loads(redis_status)
        new_status = redis_data.get("status")

        if new_status == "DONE":
            course.status         = "DONE"
            course.playlist_720p  = redis_data.get("url_720p")
            course.playlist_1080p = redis_data.get("url_1080p")
            db.session.commit()
            redis_client.delete(f"status:{course_id}")  # nettoyage

        elif new_status == "ERROR":
            course.status = "ERROR"
            db.session.commit()
            redis_client.delete(f"status:{course_id}")

    # Cas 3 : encore en cours
    return jsonify({
        "status":         course.status,
        "playlist_720p":  course.playlist_720p,
        "playlist_1080p": course.playlist_1080p
    })


# ─────────────────────────────────────────────────────────────────────────────
# MODIFIÉ — Suppression vidéo
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/delete/<int:course_id>", methods=["POST"])
def delete_course(course_id):
    course = Course.query.get_or_404(course_id)

    if course.playlist_720p:
        try:
            requests.delete(
                f"{FASTAPI_URL}/video/files",
                json={
                    "url_720p":  course.playlist_720p,
                    "url_1080p": course.playlist_1080p
                },
                timeout=10
            )
        except Exception as e:
            print(f"Erreur suppression R2 : {e}")

    # Nettoyage Redis
    redis_client.delete(f"status:{course_id}")

    db.session.delete(course)
    db.session.commit()
    return jsonify({"message": "Vidéo supprimée"})


# ─────────────────────────────────────────
# Page de lecture
# INCHANGÉ
# ─────────────────────────────────────────
@app.route("/watch/<int:course_id>")
def watch(course_id):
    course = Course.query.get_or_404(course_id)
    return render_template("watch.html", course=course)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)