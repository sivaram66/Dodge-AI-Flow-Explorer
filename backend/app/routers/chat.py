import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app import database
from app.services import guardrails, llm

router = APIRouter(prefix="/chat", tags=["chat"])

_OUT_OF_SCOPE_RESPONSE = {
    "answer": "This system is designed to answer questions related to the provided dataset only.",
    "in_scope": False,
}


class HistoryItem(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[HistoryItem] = []


@router.post("")
async def chat(req: ChatRequest):
    guard = await guardrails.check(req.message, req.history)

    if not guard.is_in_scope:
        return JSONResponse(_OUT_OF_SCOPE_RESPONSE)

    # database.pool is accessed at request time (not import time),
    # so it's always the live pool set during lifespan.
    pool = database.pool

    async def event_stream():
        async for event in llm.stream_chat(req.message, req.history, pool):
            if event["event"] == "token":
                yield {"event": "token", "data": event["data"]}

            elif event["event"] == "highlight":
                print(f"BACKEND HIGHLIGHT: {event['data']}")
                # SSE data fields must be strings; the frontend JSON.parses this.
                yield {
                    "event": "highlight",
                    "data":  json.dumps({"node_ids": event["data"]}),
                }

            elif event["event"] == "error":
                yield {"event": "error", "data": event["data"]}

    return EventSourceResponse(event_stream())
