"""
Tests for the companion service. Offline: every provider is faked, nothing needs
a key or a network.

    server/.venv/bin/python tests/test_server.py

The concurrency test runs a real uvicorn on a spare port, because the bug it
guards against -- a blocking call inside `async def` holding the event loop --
does not show up through TestClient, which serialises requests anyway.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main, providers, translate  # noqa: E402
from app.config import settings  # noqa: E402

fails: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    if not ok:
        fails.append(label)


def fake_backend(fn):
    """Swap the openai backend for `fn` and clear the cache."""
    providers._BACKENDS["openai"] = fn
    translate._cache.clear()
    settings.provider = "openai"


def mock_http(handler):
    """Route the shared client through `handler(request) -> httpx.Response`."""
    providers._client = httpx.Client(transport=httpx.MockTransport(handler))
    providers._no_temperature.clear()
    providers._no_effort.clear()


REAL_OPENAI = providers._openai


# ---------------------------------------------------------------------------

def test_requests_overlap():
    print("Server: four lines in flight take one provider-call, not four")
    def slow(system, user):
        time.sleep(1.0)
        return "ok"
    fake_backend(slow)

    srv = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=8197, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(50):
        if srv.started:
            break
        time.sleep(0.1)

    async def burst():
        async with httpx.AsyncClient(timeout=30) as c:
            t0 = time.perf_counter()
            await asyncio.gather(*[
                c.post("http://127.0.0.1:8197/translate", json={"text": f"line {i}", "target": "fa"})
                for i in range(4)])
            together = time.perf_counter() - t0

            # A summary must not freeze live translation behind it.
            t0 = time.perf_counter()
            await asyncio.gather(
                c.post("http://127.0.0.1:8197/summarize",
                       json={"speaker": "Sarah", "segments": ["a", "b"], "target": "fa"}),
                c.post("http://127.0.0.1:8197/translate", json={"text": "another", "target": "fa"}),
            )
            mixed = time.perf_counter() - t0
            return together, mixed

    together, mixed = asyncio.run(burst())
    srv.should_exit = True
    t.join(5)
    print(f"        4 parallel: {together:.2f}s   summary + line: {mixed:.2f}s")
    check("  four lines overlap (< 2s, was 4s)", together < 2.0, True)
    check("  a summary does not block a line (< 1.8s)", mixed < 1.8, True)


def test_errors_do_not_break_the_stream():
    print("Server: a provider failure comes back as a 200 with the reason")
    def boom(system, user):
        raise providers.ProviderError("OpenAI 401: bad key")
    fake_backend(boom)
    with TestClient(main.app) as c:
        r = c.post("/translate", json={"text": "hello there", "target": "fa", "key": "L1#0"})
    check("  status", r.status_code, 200)
    check("  error", r.json()["error"], "OpenAI 401: bad key")
    check("  key echoed", r.json()["key"], "L1#0")


def test_passthrough_and_cache():
    print("Server: a reader who speaks the meeting's language costs nothing")
    calls = []
    def rec(system, user):
        calls.append(user)
        return "ترجمه"
    fake_backend(rec)
    with TestClient(main.app) as c:
        r = c.post("/translate", json={"text": "hola a todos", "target": "es", "source": "es"}).json()
        check("  handed back unchanged", r["translation"], "hola a todos")
        check("  marked passthrough", r["passthrough"], True)
        check("  no provider call", len(calls), 0)

        print("Server: the cache is per target language")
        c.post("/translate", json={"text": "good morning", "target": "fa"})
        c.post("/translate", json={"text": "good morning", "target": "fa"})
        check("  repeat served from cache", len(calls), 1)
        c.post("/translate", json={"text": "good morning", "target": "ja"})
        check("  another language is its own entry", len(calls), 2)


def test_retry_and_reuse():
    settings.openai_api_key = "sk-test"
    fake_backend(REAL_OPENAI)

    print("Server: a rate limit is retried once, and recovers")
    seen = []
    def flaky(req):
        seen.append(req)
        if len(seen) == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "slow down"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "سلام"}}]})
    mock_http(flaky)
    out = providers.call("sys", "hello")[0]
    check("  translated", out, "سلام")
    check("  two attempts", len(seen), 2)

    print("Server: and given up on after one retry")
    seen.clear()
    mock_http(lambda req: (seen.append(req), httpx.Response(503, text="down"))[1])
    try:
        providers.call("sys", "hello")
        check("  raised", False, True)
    except providers.ProviderError as e:
        check("  reason shown", str(e).startswith("OpenAI 503"), True)
    check("  exactly two attempts", len(seen), 2)

    print("Server: a timeout is NOT retried -- the line is no longer live")
    seen.clear()
    def slow(req):
        seen.append(req)
        raise httpx.ReadTimeout("slow", request=req)
    mock_http(slow)
    try:
        providers.call("sys", "hello")
    except providers.ProviderError:
        pass
    check("  one attempt", len(seen), 1)

    print("Server: temperature is sent, and dropped for a model that refuses it")
    bodies = []
    def picky(req):
        import json
        b = json.loads(req.content)
        bodies.append(b)
        if "temperature" in b:
            return httpx.Response(400, json={"error": {"message": "Unsupported value: 'temperature'"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    mock_http(picky)
    check("  works", providers.call("sys", "x")[0], "ok")
    check("  first asked with temperature", "temperature" in bodies[0], True)
    check("  then without", "temperature" in bodies[1], False)
    providers.call("sys", "y")
    check("  and not asked again", "temperature" in bodies[2], False)

    print("Server: gpt-5 and later get the parameters they accept")
    bodies.clear()
    def ok(req):
        import json
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    mock_http(ok)
    settings.openai_model = "gpt-5.6-luna"
    providers.call("sys", "x")
    b = bodies[-1]
    check("  max_completion_tokens, not max_tokens", ("max_completion_tokens" in b, "max_tokens" in b), (True, False))
    check("  no thinking before answering", b.get("reasoning_effort"), "none")
    check("  no temperature (only the default is accepted)", "temperature" in b, False)

    settings.openai_model = "gpt-4o-mini"
    providers.call("sys", "x")
    b = bodies[-1]
    check("  gpt-4o-mini: temperature, no reasoning_effort",
          ("temperature" in b, "reasoning_effort" in b), (True, False))

    print("Server: a model that does not know reasoning_effort is asked without it")
    bodies.clear()
    def no_effort(req):
        import json
        b = json.loads(req.content); bodies.append(b)
        if "reasoning_effort" in b:
            return httpx.Response(400, json={"error": {"message": "Unrecognized request argument: reasoning_effort"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    mock_http(no_effort)
    settings.openai_model = "o-legacy"
    check("  works", providers.call("sys", "x")[0], "ok")
    providers.call("sys", "y")
    check("  and not offered again", "reasoning_effort" in bodies[-1], False)
    settings.openai_model = "gpt-4o-mini"

    print("Server: one connection pool for every call")
    providers._client = None
    a, b = providers.client(), providers.client()
    check("  same client", a is b, True)


test_requests_overlap()
test_errors_do_not_break_the_stream()
test_passthrough_and_cache()
test_retry_and_reuse()
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
