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

from . import languages
from .config import settings
from .providers import ProviderError
from .transcript import TranscriptError, append as append_transcript, directory as transcript_dir
from .translate import detect_language, stats, summarize_speaker, translate

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
    # The reader's language. Per request, not per server: one service serves people
    # who each want a different one, and only the reader gets to choose.
    target: str | None = None
    # What the captions look like, from /detect. A hint for the prompt, never a
    # setting -- see build_messages.
    source: str | None = None


class TranslateOut(BaseModel):
    translation: str
    cached: bool
    ms: float
    provider: str
    # True when the reader's language already matches the captions, so nothing was
    # translated. The panel uses it to show the line once rather than twice.
    passthrough: bool = False
    key: str | None = None
    error: str | None = None


@app.get("/health")
async def health():
    return {"status": "ok", "provider": settings.provider}


@app.get("/config")
async def config():
    """
    Everything the panel needs to build itself: the default language, and the full
    list to offer in the picker.

    The list lives here rather than in the extension so that adding a language is
    one line in languages.py, not an edit in two places that drift apart. The
    extension caches what it last saw, so the picker still works while this service
    is unreachable.
    """
    return {
        "target_lang": settings.target_lang,
        "target_lang_name": languages.name_of(settings.target_lang, settings.target_lang_name),
        # Persian, Arabic and Hebrew render right-to-left. Getting this wrong makes
        # the output technically correct and practically unreadable.
        "rtl": languages.is_rtl(settings.target_lang),
        "script": languages.script_of(settings.target_lang),
        "languages": languages.LANGUAGES,
        "provider": settings.provider,
        "context_segments": settings.context_segments,
        "transcript_enabled": settings.transcript_enabled,
        # Shown in the panel so people know where their meeting is being written,
        # before a file exists to point at.
        "transcript_dir": str(transcript_dir()),
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
        out = translate(req.text, req.context, req.provider, req.target, req.source)
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
    target: str | None = None


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
        return SummaryOut(**summarize_speaker(req.speaker, req.segments,
                                              req.provider, req.target))
    except ProviderError as e:
        stats["errors"] += 1
        return SummaryOut(summary="", segments=len(req.segments), ms=0.0,
                          provider=req.provider or settings.provider, error=str(e))


class DetectIn(BaseModel):
    samples: list[str] = Field(default_factory=list,
                               description="A few settled caption lines")
    provider: str | None = None


class DetectOut(BaseModel):
    lang: str = "und"
    name: str = ""
    confident: bool = False
    ms: float = 0.0
    error: str | None = None


@app.post("/detect", response_model=DetectOut)
async def do_detect(req: DetectIn):
    """
    Which language is this meeting being captioned in?

    Asked once per meeting, not per line. The answer decides the source hint sent
    with each translation, and whether this reader needs translating for at all --
    someone whose language already matches the captions should not be paying for a
    second copy of every line.
    """
    try:
        return DetectOut(**detect_language(req.samples, req.provider))
    except ProviderError as e:
        stats["errors"] += 1
        return DetectOut(error=str(e))


class Segment(BaseModel):
    id: str = Field(description="Stable per-segment id; a repeat is ignored, not duplicated")
    t: str = ""                          # ISO timestamp
    speaker: str = ""
    text: str = ""
    translation: str = ""
    # "line" is a caption; "note" is a marker in the flow of the conversation,
    # such as the reader or the meeting changing language.
    kind: str = "line"


class TranscriptIn(BaseModel):
    session: str = Field(description="Filename stem: [A-Za-z0-9_-], validated server-side")
    started_at: str = ""
    target_name: str = ""      # the reader's language, for the file header
    segments: list[Segment] = Field(default_factory=list)


class TranscriptOut(BaseModel):
    path: str = ""
    directory: str = ""
    written: int = 0
    total: int = 0
    bytes: int = 0
    error: str | None = None


@app.post("/transcript", response_model=TranscriptOut)
def save_transcript(req: TranscriptIn):
    """
    Append settled segments to this session's Markdown file.

    Deliberately `def`, not `async def`: it does blocking file IO, and FastAPI runs
    a sync endpoint in a threadpool. As `async def` it would stall the event loop
    and hold up the translations that are actually latency-sensitive.

    Errors come back as a 200 with `error` set, matching /translate — a transcript
    that cannot be written must never interrupt the caption stream.
    """
    try:
        return TranscriptOut(**append_transcript(
            req.session, req.started_at, [s.model_dump() for s in req.segments],
            req.target_name))
    except TranscriptError as e:
        return TranscriptOut(error=str(e), directory=str(transcript_dir()))
    except OSError as e:
        return TranscriptOut(error=f"cannot write transcript: {e}",
                             directory=str(transcript_dir()))
