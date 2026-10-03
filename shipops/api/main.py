"""Agent HTTP API.

Run:  uvicorn shipops.api.main:app --port 8000
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from shipops.agent.service import AgentService, TurnResult
from shipops.agent.tools import ToolContext
from shipops.api.ratelimit import DemoLimiter, client_ip
from shipops.carriers import get_carrier
from shipops.config import Settings, get_settings
from shipops.documents import DocumentStore, get_extractor
from shipops.documents.pdf import PdfError
from shipops.llm import LLMError, get_llm
from shipops.samples import SAMPLES, get_sample, sample_files

log = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=4000)


class ConfirmRequest(BaseModel):
    session_id: str = Field(max_length=64)
    approved: bool


def build_service(settings: Settings) -> AgentService:
    ctx = ToolContext(
        carrier=get_carrier(settings), documents=DocumentStore(get_extractor(settings))
    )
    return AgentService(get_llm(settings), ctx)


def public_record(rec: dict) -> dict:
    return {
        k: rec[k]
        for k in (
            "document_id",
            "filename",
            "doc_type",
            "method",
            "scanned",
            "fields",
            "issues",
            "warnings",
        )
    }


def create_app(settings: Settings | None = None, service: AgentService | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.service is None:
            try:
                app.state.service = build_service(settings)
            except LLMError as exc:  # start anyway; /api/health reports the problem
                log.error("Agent not available: %s", exc)
                app.state.startup_error = str(exc)
        yield

    app = FastAPI(title="Shipment Ops Agent API", version="0.1.0", lifespan=lifespan)
    app.state.service = service
    app.state.startup_error = None
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    limiter = DemoLimiter(settings.rate_limit_per_minute, settings.demo_daily_request_cap)

    def limited(request: Request) -> None:
        """Demo mode: per-IP and daily caps on endpoints that spend LLM / carrier calls."""
        if not settings.demo_mode:
            return
        ok, retry, reason = limiter.check(client_ip(request, settings.trust_proxy))
        if not ok:
            raise HTTPException(429, reason, headers={"Retry-After": str(retry)})

    def svc(request: Request) -> AgentService:
        s = request.app.state.service
        if s is None:
            raise HTTPException(503, request.app.state.startup_error or "Agent not ready")
        return s

    @app.get("/api/health")
    def health(request: Request) -> dict:
        return {
            "status": "ok" if request.app.state.service else "degraded",
            "error": request.app.state.startup_error,
            "llm_provider": settings.llm_provider,
            "carrier": "mock" if settings.demo_mode else settings.carrier,
            "demo_mode": settings.demo_mode,
            "max_upload_mb": settings.max_upload_mb,
        }

    @app.post("/api/chat", response_model=TurnResult, dependencies=[Depends(limited)])
    def chat(body: ChatRequest, request: Request) -> TurnResult:
        session_id = body.session_id or uuid.uuid4().hex
        return svc(request).chat(session_id, body.message.strip())

    @app.post("/api/chat/confirm", response_model=TurnResult, dependencies=[Depends(limited)])
    def confirm(body: ConfirmRequest, request: Request) -> TurnResult:
        return svc(request).confirm(body.session_id, body.approved)

    @app.post("/api/documents", dependencies=[Depends(limited)])
    def upload_document(request: Request, file: UploadFile = File(...)) -> dict:
        store = svc(request).ctx.documents
        limit = int(settings.max_upload_mb * 1024 * 1024)
        data = file.file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, f"File too large (max {settings.max_upload_mb:g} MB)")
        if not data.startswith(b"%PDF"):
            raise HTTPException(415, "Only PDF files are supported")
        try:
            rec = store.add(file.filename or "upload.pdf", data)
        except PdfError as exc:
            raise HTTPException(422, str(exc)) from exc
        return public_record(rec)

    @app.get("/api/samples")
    def list_samples() -> list[dict]:
        return [
            {k: s[k] for k in ("id", "title", "prompt")} | {"files": len(s["files"])}
            for s in SAMPLES
        ]

    @app.post("/api/samples/{sample_id}", dependencies=[Depends(limited)])
    def load_sample(sample_id: str, request: Request) -> dict:
        sample = get_sample(sample_id)
        if sample is None:
            raise HTTPException(404, f"Unknown sample '{sample_id}'")
        store = svc(request).ctx.documents
        docs = [public_record(store.add(name, data)) for name, data in sample_files(sample)]
        return {"id": sample_id, "prompt": sample["prompt"], "documents": docs}

    return app


app = create_app()
