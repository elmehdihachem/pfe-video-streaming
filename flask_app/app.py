import os
import requests
from flask import Flask, render_template, request, redirect, url_for, jsonify,session
from models import db, Course

app = Flask(__name__)
app.secret_key = "lms_secret_key"

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


# ─────────────────────────────────────────
# Page principale — liste des vidéos
# ─────────────────────────────────────────
@app.route("/")
def index():
    courses = Course.query.order_by(Course.created_at.desc()).all()
    return render_template("index.html", courses=courses)
#@app.route("/")
#def index():
   # courses = Course.query.order_by(Course.created_at.desc()).all()
   # return render_template("index.html", courses=courses)


# ─────────────────────────────────────────
# Upload vidéo
# ─────────────────────────────────────────
#@app.route("/upload", methods=["POST"])
#def upload():
    #titre = request.form.get("titre")
    #file  = request.files.get("video")

    #if not titre or not file:
       # return "Titre et vidéo requis", 400

    # Envoie vers FastAPI /upload
    #response = requests.post(
        #f"{FASTAPI_URL}/upload",
        #files={"file": (file.filename, file.stream, file.content_type)}
    #)

    #if response.status_code != 200:
      #  return f"Erreur FastAPI: {response.text}", 500

    #data     = response.json()
   # video_id = data.get("video_id")

    # Crée l'entrée dans la table courses
    #course = Course(
        #titre    = titre,
        #video_id = video_id,
        #status   = "PENDING"
     #)
    #db.session.add(course)
    #db.session.commit()

    #return redirect(url_for("index"))

    # ─────────────────────────────────────────
    # Page 2 — Titre + Thumbnail
    # ─────────────────────────────────────────
@app.route("/upload/info", methods=["GET", "POST"])
def upload_info():
    if request.method == "POST":
        titre = request.form.get("titre")
        thumbnail = request.files.get("thumbnail")

        thumbnail_url = None

        if thumbnail and thumbnail.filename:
            # Upload thumbnail vers R2
            files = {"file": (thumbnail.filename, thumbnail.stream, thumbnail.content_type)}
            response = requests.post(f"{FASTAPI_URL}/upload/thumbnail", files=files)
            if response.status_code == 200:
                thumbnail_url = response.json().get("thumbnail_url")

        # Sauvegarde en session pour la page suivante
        session["titre"] = titre
        session["thumbnail_url"] = thumbnail_url

        return redirect(url_for("upload_video"))

    return render_template("upload_info.html")

# ─────────────────────────────────────────
# Page 3 — Upload vidéo avec progression
# ─────────────────────────────────────────
@app.route("/upload/video")
def upload_video():
    titre = session.get("titre")
    thumbnail_url = session.get("thumbnail_url")
    if not titre:
        return redirect(url_for("upload_info"))
    return render_template("upload_video.html", titre=titre, thumbnail_url=thumbnail_url)

# ─────────────────────────────────────────
# API — Upload vidéo avec XMLHttpRequest (progression)
# ─────────────────────────────────────────
@app.route("/api/upload/video", methods=["POST"])
def api_upload_video():
    titre = request.form.get("titre")
    thumbnail_url = request.form.get("thumbnail_url")
    file = request.files.get("video")

    if not file:
         return jsonify({"error": "Vidéo requise"}), 400

    # Envoie vers FastAPI
    response = requests.post(
    f"{FASTAPI_URL}/upload",
        files={"file": (file.filename, file.stream, file.content_type)}
    )

    if response.status_code != 200:
        return jsonify({"error": response.text}), 500

    data = response.json()
    video_id = data.get("video_id")

    course = Course(
        titre=titre,
        thumbnail_url=thumbnail_url,
        video_id=video_id,
        status="PENDING"
    )
    db.session.add(course)
    db.session.commit()

    # Nettoie la session
    session.pop("titre", None)
    session.pop("thumbnail_url", None)

    return jsonify({
        "message": "Upload terminé ! Conversion en cours...",
        "course_id": course.id,
        "video_id": video_id
    })
# ─────────────────────────────────────────
# ✅ Webhook — reçoit la notification de FastAPI
# ─────────────────────────────────────────
@app.route("/webhooks/video-ready", methods=["POST"])
def webhook_video_ready():
    data     = request.get_json()
    video_id = data.get("video_id")
    url_720p  = data.get("url_720p")
    url_1080p = data.get("url_1080p")

    course = Course.query.filter_by(video_id=video_id).first()
    if not course:
        return jsonify({"error": "Course introuvable"}), 404

    course.status = "DONE"
    course.playlist_720p = url_720p
    course.playlist_1080p = url_1080p
    db.session.commit()

    return jsonify({"message": "OK"}), 200

    #if not video_id:
        #return jsonify({"error": "video_id manquant"}), 400

    # Trouve le cours correspondant
    #course = Course.query.filter_by(video_id=video_id).first()
    #if not course:
        #return jsonify({"error": "Course introuvable"}), 404

    # Met à jour la table courses
    #course.status        = "DONE"
   # course.playlist_720p  = url_720p
   # course.playlist_1080p = url_1080p
    #db.session.commit()

    #return jsonify({"message": "OK"}), 200


# ─────────────────────────────────────────
# Poll statut — appelé en AJAX depuis la page
# ─────────────────────────────────────────
@app.route("/status/<int:course_id>")
def check_status(course_id):
    course = Course.query.get_or_404(course_id)

    # ✅ Si déjà DONE ou ERROR — retourne directement sans contacter FastAPI
    if course.status in ["DONE", "ERROR"]:
        return jsonify({
            "status":         course.status,
            "playlist_720p":  course.playlist_720p,
            "playlist_1080p": course.playlist_1080p
        })

    # ✅ Sinon — demande le statut à FastAPI avec gestion d'erreur
    try:
        response = requests.get(
            f"{FASTAPI_URL}/video/{course.video_id}",
            timeout=5
        )

        if response.status_code == 200:
            data = response.json()
            new_status = data.get("status", course.status)

            # Met à jour seulement si le statut a changé
            if new_status != course.status:
                course.status = new_status

            if data.get("playlist_720p"):
                course.playlist_720p  = data.get("playlist_720p")
                course.playlist_1080p = data.get("playlist_1080p")

            db.session.commit()

    except Exception as e:
        # ✅ Si FastAPI ne répond pas — retourne le statut actuel sans erreur
        print(f"Poll error: {e}")

    return jsonify({
        "status":         course.status,
        "playlist_720p":  course.playlist_720p,
        "playlist_1080p": course.playlist_1080p
    })
@app.route("/delete/<int:course_id>", methods=["POST"])
def delete_course(course_id):
    course = Course.query.get_or_404(course_id)

    # ✅ Supprime les fichiers de R2 via FastAPI
    if course.video_id:
        try:
            requests.delete(
                f"{FASTAPI_URL}/video/{course.video_id}/files",
                timeout=10
            )
        except Exception as e:
            print(f"Erreur suppression R2: {e}")

    db.session.delete(course)
    db.session.commit()
    return jsonify({"message": "Vidéo supprimée"})




# ─────────────────────────────────────────
# Page de lecture
# ─────────────────────────────────────────
@app.route("/watch/<int:course_id>")
def watch(course_id):
    course = Course.query.get_or_404(course_id)
    return render_template("watch.html", course=course)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)