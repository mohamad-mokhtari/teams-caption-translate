"""
One translation call, three possible backends.

Same shape as main-app/app/llm.py: everything goes through translate(), so swapping
provider is a config change rather than an edit. That matters more than usual here —
some meetings will be too sensitive for a cloud provider, and the local path has to
be a switch, not a rewrite.
"""
from __future__ import annotations

import time

import httpx

from .config import settings


class ProviderError(Exception):
    """Safe to show a user — never contains a key."""


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
    try:
        r = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": settings.openai_model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "max_tokens": 800,
            },
            timeout=settings.timeout_seconds,
        )
    except httpx.RequestError as e:
        raise ProviderError(f"OpenAI unreachable: {e.__class__.__name__}") from e
    if r.status_code != 200:
        raise ProviderError(f"OpenAI {r.status_code}: {r.text[:160]}")
    return r.json()["choices"][0]["message"]["content"]


def _ollama(system: str, user: str) -> str:
    """Local. Nothing leaves the machine — the option for sensitive meetings."""
    try:
        r = httpx.post(
            f"{settings.ollama_url.rstrip('/')}/api/chat",
            json={
                "model": settings.ollama_model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "stream": False,
                # Translation wants determinism, not creativity.
                "options": {"temperature": 0.2},
            },
            timeout=settings.timeout_seconds,
        )
    except httpx.RequestError as e:
        raise ProviderError(
            f"Ollama unreachable at {settings.ollama_url} — is it running? "
            f"({e.__class__.__name__})"
        ) from e
    if r.status_code != 200:
        raise ProviderError(f"Ollama {r.status_code}: {r.text[:160]}")
    return r.json().get("message", {}).get("content", "")


def _anthropic(system: str, user: str) -> str:
    if not settings.anthropic_api_key:
        raise ProviderError("ANTHROPIC_API_KEY is not set")
    try:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": settings.anthropic_model,
                "max_tokens": 800,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=settings.timeout_seconds,
        )
    except httpx.RequestError as e:
        raise ProviderError(f"Anthropic unreachable: {e.__class__.__name__}") from e
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
