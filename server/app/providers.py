"""
One translation call, three possible backends.

Same shape as main-app/app/llm.py: everything goes through translate(), so swapping
provider is a config change rather than an edit. That matters more than usual here —
some meetings will be too sensitive for a cloud provider, and the local path has to
be a switch, not a rewrite.
"""
from __future__ import annotations

import threading
import time

import httpx

from .config import settings


class ProviderError(Exception):
    """Safe to show a user — never contains a key."""


# One connection pool for the life of the service.
#
# A fresh httpx.post() per caption meant a fresh TCP + TLS handshake to the
# provider for every line -- typically 100-300 ms of a 2-second budget, spent on
# nothing. A shared client keeps the connection warm between lines. httpx.Client
# is safe to share between the threads FastAPI runs requests on.
_client: httpx.Client | None = None
_client_lock = threading.Lock()


def client() -> httpx.Client:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(
                    timeout=settings.timeout_seconds,
                    limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=60),
                )
    return _client


# Worth one more try: the provider said "not right now", or the connection never
# got made. NOT a timeout -- a line that has already taken 20 s is no longer live,
# and trying again only delays the next one.
_RETRY_STATUS = {429, 500, 502, 503, 504, 529}
_RETRY_PAUSE_S = 0.4


def _post(name: str, url: str, **kw) -> httpx.Response:
    """POST with one quick retry on a transient failure."""
    for attempt in range(settings.retries + 1):
        last = attempt == settings.retries
        try:
            r = client().post(url, **kw)
        except httpx.ConnectError as e:
            if last:
                raise ProviderError(f"{name} unreachable: {e.__class__.__name__}") from e
            time.sleep(_RETRY_PAUSE_S)
            continue
        except httpx.RequestError as e:
            raise ProviderError(f"{name} unreachable: {e.__class__.__name__}") from e
        if r.status_code in _RETRY_STATUS and not last:
            # Honour a short Retry-After; a long one means the line is lost anyway.
            try:
                wait = min(float(r.headers.get("retry-after", _RETRY_PAUSE_S)), 2.0)
            except ValueError:
                wait = _RETRY_PAUSE_S
            time.sleep(max(wait, 0.0))
            continue
        return r
    raise AssertionError("unreachable")  # pragma: no cover


# Models that refused a temperature once are not asked again. Some newer OpenAI
# models accept only their default and reject the request outright otherwise.
_no_temperature: set[str] = set()
_no_effort: set[str] = set()


def _is_reasoning_model(model: str) -> bool:
    """gpt-5 and later, and the o-series. The gpt-4 / gpt-3.5 generation is not."""
    m = model.lower()
    return not m.startswith(("gpt-4", "gpt-3.5", "chatgpt-4"))


def _clean(text: str) -> str:
    """
    Strip the preamble small models sometimes add.

    Asking for "only the translation" works most of the time; this catches the rest.
    Cheaper and lower-latency than forcing structured output for a single string.
    """
    t = (text or "").strip()
    for prefix in ("Translation:", "translation:", "ترجمه:", "Here is the translation:"):
        if t.startswith(prefix):
            t = t[len(prefix):].strip()
    if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'«»":
        t = t[1:-1].strip()
    return t


def _openai(system: str, user: str) -> str:
    if not settings.openai_api_key:
        raise ProviderError("OPENAI_API_KEY is not set")
    model = settings.openai_model
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        # Not `max_tokens`: gpt-5 and later reject it outright. Every model,
        # gpt-4o-mini included, accepts this one.
        "max_completion_tokens": 800,
    }
    if _is_reasoning_model(model):
        # gpt-5 onwards think before answering unless told not to. For a caption
        # that is seconds of delay and paid tokens for no better translation.
        if settings.openai_reasoning_effort and model not in _no_effort:
            body["reasoning_effort"] = settings.openai_reasoning_effort
    elif settings.temperature is not None and model not in _no_temperature:
        # Translation wants the same wording every time, not variety: without this
        # a retranslated line can come back phrased differently for no reason.
        # Only the gpt-4 generation: reasoning models accept just their default.
        body["temperature"] = settings.temperature
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    url = "https://api.openai.com/v1/chat/completions"

    r = _post("OpenAI", url, headers=headers, json=body)
    # A model that refuses one of the optional settings is asked again without
    # it, and not offered it again.
    for field, refused in (("temperature", _no_temperature), ("reasoning_effort", _no_effort)):
        if r.status_code == 400 and field in body and field in r.text:
            refused.add(model)
            body.pop(field)
            r = _post("OpenAI", url, headers=headers, json=body)
    if r.status_code != 200:
        raise ProviderError(f"OpenAI {r.status_code}: {r.text[:160]}")
    return r.json()["choices"][0]["message"]["content"]


def _ollama(system: str, user: str) -> str:
    """Local. Nothing leaves the machine — the option for sensitive meetings."""
    try:
        r = _post(
            "Ollama",
            f"{settings.ollama_url.rstrip('/')}/api/chat",
            json={
                "model": settings.ollama_model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "stream": False,
                # Translation wants determinism, not creativity.
                "options": {"temperature": 0.2 if settings.temperature is None
                            else settings.temperature},
            },
        )
    except ProviderError as e:
        raise ProviderError(
            f"Ollama unreachable at {settings.ollama_url} — is it running? ({e})"
        ) from e
    if r.status_code != 200:
        raise ProviderError(f"Ollama {r.status_code}: {r.text[:160]}")
    return r.json().get("message", {}).get("content", "")


def _anthropic(system: str, user: str) -> str:
    if not settings.anthropic_api_key:
        raise ProviderError("ANTHROPIC_API_KEY is not set")
    body = {
        "model": settings.anthropic_model,
        "max_tokens": 800,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if settings.temperature is not None:
        body["temperature"] = settings.temperature
    r = _post(
        "Anthropic",
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=body,
    )
    if r.status_code != 200:
        raise ProviderError(f"Anthropic {r.status_code}: {r.text[:160]}")
    blocks = r.json().get("content", [])
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


_BACKENDS = {"openai": _openai, "ollama": _ollama, "anthropic": _anthropic}


def call(system: str, user: str, provider: str | None = None) -> tuple[str, float, str]:
    """Returns (text, elapsed_ms, provider_used)."""
    name = (provider or settings.provider).lower()
    fn = _BACKENDS.get(name)
    if fn is None:
        raise ProviderError(f"Unknown provider '{name}'. Use: {', '.join(_BACKENDS)}")
    t0 = time.perf_counter()
    out = fn(system, user)
    return _clean(out), (time.perf_counter() - t0) * 1000, name
