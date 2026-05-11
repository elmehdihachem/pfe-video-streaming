from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Course(db.Model):
    __tablename__ = "courses"

    id              = db.Column(db.Integer, primary_key=True)
    titre           = db.Column(db.String(255), nullable=False)
    thumbnail_url   = db.Column(db.String(500), nullable=True)
    playlist_720p   = db.Column(db.String(500), nullable=True)
    playlist_1080p  = db.Column(db.String(500), nullable=True)
    status          = db.Column(db.String(50), default="PENDING")
    video_id        = db.Column(db.Integer, nullable=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)