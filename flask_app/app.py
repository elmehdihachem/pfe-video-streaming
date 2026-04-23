import os
import requests
from flask import Flask, render_template, request, redirect, url_for, jsonify
from models import db, Course

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = (
    f"mysql+pymysql://{os.getenv('MYSQL_USER')}:{os.getenv('MYSQL_PASSWORD')}"
    f"@{os.getenv('MYSQL_HOST')}/{os.getenv('MYSQL_DATABASE')}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

FASTAPI_URL = os.getenv("FASTAPI_URL", "http://api:8000/api/v1")

db.init_app(app)

with app.app_context():
    db.create_all()


# ─────────────────────────────────────────
# Page principale — liste des vidéos + formulaire upload
# ─────────────────────────────────────────
@app.route("/")
def index():
    courses = Course.query.order_by(Course.created_at.desc()).all()
    return render_template("index.html", courses=courses)


# ─────────────────────────────────────────
# Upload vidéo
# ─────────────────────────────────────────
@app.route("/upload", methods=["POST"])
def upload():
    titre = request.form.get("titre")
    file  = request.files.get("video")

    if not titre or not file:
        return "Titre et vidéo requis", 400

    # Envoie vers FastAPI /upload
    response = requests.post(
        f"{FASTAPI_URL}/upload",
        files={"file": (file.filename, file.stream, file.content_type)}
    )

    if response.status_code != 200:
        return f"Erreur FastAPI: {response.text}", 500

    data     = response.json()
    video_id = data.get("video_id")

    # Crée l'entrée dans la table courses
    course = Course(
        titre    = titre,
        video_id = video_id,
        status   = "PENDING"
    )
    db.session.add(course)
    db.session.commit()

    return redirect(url_for("index"))
# ─────────────────────────────────────────
# ✅ Webhook — reçoit la notification de FastAPI
# ─────────────────────────────────────────
@app.route("/webhooks/video-ready", methods=["POST"])
def webhook_video_ready():
    data     = request.get_json()
    video_id = data.get("video_id")
    url_720p  = data.get("url_720p")
    url_1080p = data.get("url_1080p")

    if not video_id:
        return jsonify({"error": "video_id manquant"}), 400

    # Trouve le cours correspondant
    course = Course.query.filter_by(video_id=video_id).first()
    if not course:
        return jsonify({"error": "Course introuvable"}), 404

    # Met à jour la table courses
    course.status        = "DONE"
    course.playlist_720p  = url_720p
    course.playlist_1080p = url_1080p
    db.session.commit()

    return jsonify({"message": "OK"}), 200


# ─────────────────────────────────────────
# Poll statut — appelé en AJAX depuis la page
# ─────────────────────────────────────────
@app.route("/status/<int:course_id>")
def check_status(course_id):
    course = Course.query.get_or_404(course_id)

    if course.status == "DONE":
        return jsonify({
            "status":        "DONE",
            "playlist_720p":  course.playlist_720p,
            "playlist_1080p": course.playlist_1080p
        })

    # Demande le statut à FastAPI
    #response = requests.get(f"{FASTAPI_URL}/video/{course.video_id}")
    #if response.status_code != 200:
        #return jsonify({"status": "ERROR"}), 500

   # data = response.json()

    # Met à jour la table courses
    #course.status = data.get("status", "PENDING")
    #if data.get("playlist_720p"):
       # course.playlist_720p  = data.get("playlist_720p")
       # course.playlist_1080p = data.get("playlist_1080p")
   # db.session.commit()

    #return jsonify({
     #   "status":        course.status,
       # "playlist_720p":  course.playlist_720p,
       # "playlist_1080p": course.playlist_1080p
   # })


# ─────────────────────────────────────────
# Page de lecture
# ─────────────────────────────────────────
@app.route("/watch/<int:course_id>")
def watch(course_id):
    course = Course.query.get_or_404(course_id)
    return render_template("watch.html", course=course)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)