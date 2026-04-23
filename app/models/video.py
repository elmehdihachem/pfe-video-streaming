import enum
from sqlalchemy import Column, Integer, String, DateTime, Enum
from sqlalchemy.sql import func

from database.db import Base


class VideoStatus(str, enum.Enum):
    PENDING   = "PENDING"
    ENCODING  = "ENCODING"
    UPLOADING = "UPLOADING"
    DONE      = "DONE"
    ERROR     = "ERROR"

class Video(Base):
    __tablename__ = "videos"

    id           = Column(Integer, primary_key=True, index=True)
    nom          = Column(String(255), nullable=False)
    playlist_url = Column(String(500), nullable=True)      #720p par defaut
    playlist_url_1080 = Column(String(500), nullable=True)  # 1080p
    status       = Column(Enum(VideoStatus), default=VideoStatus.PENDING)
    created_at   = Column(DateTime, server_default=func.now())