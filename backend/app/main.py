from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import close_pool, init_pool
from app.services import graph_builder

import os

@asynccontextmanager
async def lifespan(app: FastAPI):
    db_pool = await init_pool()
    graph_builder.graph = await graph_builder.build_graph(db_pool)
    yield
    await close_pool()


app = FastAPI(title="DodgeAI Flow Explorer", lifespan=lifespan)

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

origins = [
    "http://localhost:5173",  # dev
    FRONTEND_URL             # prod
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.routers import chat, graph

app.include_router(graph.router, prefix="/api")
app.include_router(chat.router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok"}
