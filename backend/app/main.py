import asyncio
import logging
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Annotated
from weakref import WeakValueDictionary

import litellm
from fastapi import FastAPI, Header, HTTPException, Path, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints, field_validator

from .config import Settings, csv_env
from .gateway import Gateway, ProvidersUnavailable, simulated_failures
from .storage import ChatStore

logger = logging.getLogger(__name__)
DEFAULT_PROMPT = "You are a helpful assistant. Give clear, accurate answers."
SessionId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128)]
MessageText = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=4000)
]


class ChatRequest(BaseModel):
    session_id: SessionId
    message: MessageText
    system_prompt: Annotated[str, StringConstraints(strict=True)] | None = None

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("session_id cannot be blank")
        return value


def create_app(settings: Settings | None = None):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        current = settings if settings is not None else Settings.from_env()
        litellm.suppress_debug_info = True
        litellm.telemetry = False
        app.state.store = ChatStore(current.database_path)
        await asyncio.to_thread(app.state.store.initialize)
        app.state.gateway = Gateway(current)
        app.state.settings = current
        app.state.locks = WeakValueDictionary()
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def handle_unexpected_error(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as error:
            logger.error("Request failed (%s)", type(error).__name__)
            return JSONResponse(status_code=500, content={"detail": "internal server error"})

    @app.get("/health")
    async def health():
        try:
            await asyncio.to_thread(app.state.store.check)
        except Exception as error:
            raise HTTPException(503, "database unavailable") from error
        return {
            "status": "ok",
            "keys_configured": len(app.state.gateway.keys),
            "cache_enabled": True,
        }

    @app.post("/chat")
    async def chat(
        payload: ChatRequest,
        simulation: Annotated[str | None, Header(alias="X-Simulate-Failure")] = None,
    ):
        started = perf_counter()
        try:
            failures = simulated_failures(simulation, app.state.settings.enable_failure_simulation)
            valid_keys = {key.id for key in app.state.gateway.keys} | {"*"}
            if failures.keys() - valid_keys:
                raise ValueError("X-Simulate-Failure contains an unknown key id")
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

        store = app.state.store
        lock = app.state.locks.get(payload.session_id)
        if lock is None:
            lock = asyncio.Lock()
            app.state.locks[payload.session_id] = lock
        async with lock:
            saved_prompt, history = await asyncio.to_thread(store.load, payload.session_id)
            prompt = payload.system_prompt if payload.system_prompt is not None else saved_prompt
            if not prompt:
                prompt = DEFAULT_PROMPT
            messages = [
                {"role": "system", "content": prompt},
                *({"role": item["role"], "content": item["content"]} for item in history),
                {"role": "user", "content": payload.message},
            ]
            try:
                resolved = await app.state.gateway.complete(messages, failures)
            except ProvidersUnavailable as error:
                raise HTTPException(503, "all providers unavailable") from error

            metadata = {
                "resolution_source": "LLM_CACHE" if resolved.key_id is None else "LIVE_INFERENCE",
                "model": resolved.model,
                "latency_ms": int((perf_counter() - started) * 1000),
                "input_tokens": resolved.completion.input_tokens,
                "output_tokens": resolved.completion.output_tokens,
                "key_id": resolved.key_id,
                "fallback_attempts": resolved.fallback_attempts,
            }
            reply = resolved.completion.reply
            await asyncio.to_thread(
                store.save_turn, payload.session_id, prompt, payload.message, reply, metadata
            )
        return {"session_id": payload.session_id, "reply": reply, "metadata": metadata}

    @app.get("/sessions/{session_id:path}/messages")
    async def session_messages(session_id: Annotated[str, Path(min_length=1, max_length=128)]):
        _, messages = await asyncio.to_thread(app.state.store.load, session_id)
        if not messages:
            raise HTTPException(404, "session not found")
        return {"session_id": session_id, "messages": messages}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=(
            settings.allowed_origins if settings else
            csv_env("ALLOWED_ORIGINS", "http://localhost:3000")
        ),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Simulate-Failure"],
    )
    return app


app = create_app()
