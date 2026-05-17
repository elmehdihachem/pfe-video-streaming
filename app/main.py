import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database.db import engine, Base
from routes.video import router as video_router


from services.background import listen_redis

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)

    # ─────────────────────────────────────────────────────────────────────
    #  — Lance listen_redis() en arrière-plan au démarrage
    # ─────────────────────────────────────────────────────────────────────
    asyncio.create_task(listen_redis())

    yield

app = FastAPI(
    title="Streaming API",
    description="API de gestion et streaming de vidéos",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(video_router, prefix="/api/v1", tags=["Videos"])

@app.get("/")
def home():
    return {"message": "Bienvenue sur l'API de streaming !"}