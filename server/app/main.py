"""
Translation companion for the caption extension.

Runs on 127.0.0.1 next to the browser. The extension posts each settled caption
segment here and renders what comes back.

Why a local service instead of calling the LLM from the extension:
  - API keys live in a .env file, not in browser storage on every laptop
  - provider switching is Python, shared with the rest of the lab's patterns
  - Ollama is reachable without fighting CORS from a page origin
  - latency and cost are measurable in one place

Run:  ./run.sh
"""
from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import settings
from .providers import ProviderError
from .translate import stats, summarize_speaker, translate

app = FastAPI(title="Caption Translator", version="1.0.0")

# The extension's fetch carries the Teams page origin, so CORS applies. Listing the
# Teams hosts rather than "*" keeps a random site from using this service — it is
# listening on the developer's own machine.
#
# Note the browser does NOT block http://127.0.0.1 from an https page: localhost is
# a "potentially trustworthy" origin, so mixed-content rules don't apply. That is
# what makes a plain-HTTP local companion viable at all.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://teams.cloud.microsoft",
        "https://teams.microsoft.com",
        "https://teams.live.com",
    ],
    allow_origin_regex=r"https://.*\.(cloud\.microsoft|teams\.microsoft\.com)",
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
    # Chrome/Edge Private Network Access: a page on a public origin
    # (teams.cloud.microsoft) reaching a private address (127.0.0.1) sends
    # `Access-Control-Request-Private-Network: true` on the preflight, and refuses
    # the real request unless the response allows it. Without this the preflight
    # returns 400 "Disallowed CORS private-network" and the panel shows no
    # translations, with only an opaque CORS message in the console.
    allow_private_network=True,
)


class TranslateIn(BaseModel):
    text: str = Field(description="One settled caption segment")
    context: list[str] = Field(default_factory=list, description="Preceding segments")
    speaker: str = ""
    provider: str | None = None          # override per request, for A/B testing
    key: str | None = None               # caption line id; echoed back for replacement


class TranslateOut(BaseModel):
    translation: str
    cached: bool
    ms: float
    provider: str
    key: str | None = None
    error: str | None = None


@app.get("/health")
async def health():
    return {"status": "ok", "provider": settings.provider}


@app.get("/config")
async def config():
    """The extension reads this on startup so language and direction aren't hardcoded in JS."""
    return {
        "target_lang": settings.target_lang,
        "target_lang_name": settings.target_lang_name,
        # Persian, Arabic, Hebrew and Urdu render right-to-left. Getting this wrong
        # makes the output technically correct and practically unreadable.
        "rtl": settings.target_lang in {"fa", "ar", "he", "ur", "ps", "sd"},
        "provider": settings.provider,
        "context_segments": settings.context_segments,
    }


@app.get("/stats")
async def get_stats():
    avg = stats["total_ms"] / stats["calls"] if stats["calls"] else 0.0
    return {**stats, "avg_ms": round(avg, 1),
            "cache_hit_rate": round(stats["cache_hits"] /
                                    max(stats["cache_hits"] + stats["calls"], 1), 3)}


@app.post("/translate", response_model=TranslateOut)
async def do_translate(req: TranslateIn):
    """
    Errors are returned as a 200 with an `error` field rather than an HTTP error.

    A failed translation must not break the caption stream — the reader still wants
    the English, and the panel should show why one line is missing instead of the
    whole thing going silent.
    """
    t0 = time.perf_counter()
    try:
        out = translate(req.text, req.context, req.provider)
        return TranslateOut(**out, key=req.key)
    except ProviderError as e:
        stats["errors"] += 1
        return TranslateOut(
            translation="", cached=False,
            ms=round((time.perf_counter() - t0) * 1000, 1),
            provider=req.provider or settings.provider,
            key=req.key, error=str(e),
        )


class SummaryIn(BaseModel):
    speaker: str = ""
    segments: list[str] = Field(default_factory=list,
                                description="Everything this speaker said, in order")
    provider: str | None = None


class SummaryOut(BaseModel):
    summary: str
    segments: int
    ms: float
    provider: str
    error: str | None = None


@app.post("/summarize", response_model=SummaryOut)
async def do_summarize(req: SummaryIn):
    """
    Summarise one speaker's contributions.

    Slower and more expensive than a translation — it reads the whole transcript for
    that person — so it is user-triggered, never automatic.
    """
    try:
        return SummaryOut(**summarize_speaker(req.speaker, req.segments, req.provider))
    except ProviderError as e:
        stats["errors"] += 1
        return SummaryOut(summary="", segments=len(req.segments), ms=0.0,
                          provider=req.provider or settings.provider, error=str(e))
